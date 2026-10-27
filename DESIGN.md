# daybrief 설계

## 1. 레포 구조

| 레포 | 공개 | 내용 |
|---|---|---|
| `daybrief` (이 레포) | public | 엔진 코드, 소스 플러그인, 대시보드 템플릿, `examples/profile.example.yaml` |
| `daybrief-me` | private | 본인 `profile.yaml`, 저널 목록, 수집 결과(`data/`), 진행 요약, 로드맵 상태, GitHub Actions 수집기 |

- `daybrief-me`는 엔진을 `pip install git+https://github.com/<you>/daybrief` 로 가져온다. 엔진 코드를 복사하지 않는다.
- 기능 수정은 항상 `daybrief`에서. `daybrief-me`에는 설정과 데이터만.
- 랩원은 `daybrief-me`를 템플릿 삼아 자기 private 레포를 만든다 (`daybrief init` 명령으로 생성 예정).

## 2. 실행 위치 (기기 동기화 해결 방식)

```
[GitHub Actions, daybrief-me]      매일 06:00 KST
  sources 1~5 수집 → filter → summarize → data/YYYY-MM-DD.json 커밋
                                   │
                         (git pull, 로그인 시)
                                   ▼
[각 Windows 기기]  작업 스케줄러 "로그온 시"
  ① git pull daybrief-me
  ② 로컬 전용 소스(6번: Claude Code 로그) 수집 → 요약본만 data/progress/에 커밋·push
  ③ render → dashboard/index.html 갱신 → Lively Wallpaper가 표시
```

- 1~5번: 기기 무관 → 클라우드에서 1회만.
- 6번: 기기별. **원본 로그는 기기 밖으로 나가지 않는다.** 요약본만 push.
- 프로젝트 식별자는 절대경로가 아니라 **git remote URL** (노트북/데스크탑 경로가 달라도 같은 프로젝트로 합쳐짐).
- 3번(Notion 게스트)이 로그인 세션을 요구하면 그 소스만 로컬 실행으로 분류 (`run_on: local`).

## 3. 핵심 데이터 모델

```python
Item:            # 수집 단위. 모든 소스가 이것만 반환
  id             # f"{source}:{안정적 원천 ID}" — 중복 제거 키
  source         # 플러그인 이름
  kind           # news | notice | paper | page_change | progress
  title, url     # url 필수
  published_at   # 원천 데이터의 날짜 (LLM 추정 금지)
  body           # 필터·요약에 쓸 원문 텍스트 (초록, 공지 본문 등)
  meta: dict     # 저널명, DOI, 저자, 마감일 등 소스별 구조화 필드

Judgement:       # LLM 필터 결과
  item_id, relevant: bool, score: 0~1, reason: str, model: str

Summary:         # 정밀 요약 (5번 등)
  item_id, text, basis: "abstract"|"fulltext"|"page", model

RoadmapProposal: # 7번. 승인 전까지 로드맵에 반영 안 됨
  project, change, evidence: [commit/session 링크], status: pending|accepted|rejected
```

저장소: `data/daybrief.sqlite` (상태·중복 제거) + 날짜별 `data/digest/YYYY-MM-DD.json` (렌더링·git 동기화용).
SQLite는 git에 올리지 않고 JSON만 올린다 (머지 충돌 방지).

## 4. 소스 플러그인 인터페이스

```python
class Source(Protocol):
    name: str
    run_on: Literal["cloud", "local"]
    def fetch(self, since: datetime, cfg: dict) -> list[Item]: ...
```

`@register("openalex")` 데코레이터로 등록. 설정은 `profile.yaml`의 `sources.<name>`.

| 소스 | 항목 | 방식 | 상태 |
|---|---|---|---|
| `openalex` | 4,5 | OpenAlex `/works` API, ISSN 필터 + `from_publication_date` | 골격 구현 |
| `html_list` | 1,2 | 범용 게시판 스크래퍼. 설정의 CSS 셀렉터로 목록/제목/날짜/링크 추출 | 골격 구현 |
| `public_api` | 2 | 공공데이터포털·온통청년 등 Open API | 2단계 |
| `notion_page` | 3 | ① notion.site 공개면 Playwright 렌더링 ② 아니면 공식 Notion API/MCP 접근 가능 여부 먼저 확인 ③ 최후 수단: 로그인 프로필 Playwright (local). **연구실이 Notion을 정적 사이트로 내보내면 `html_list`(kind: page_change)로 충분** (2026-10-27 적용) | 4단계 |
| `job_alio` | 채용 | 공공기관 채용정보 오픈API (잡알리오, 2024년부터 개방). 정부출연연·공기업 | 2단계 |
| `job_boards` | 채용 | 기업 채용 페이지 (html_list 설정 재사용, JS 렌더링이면 playwright). 민간 채용 플랫폼은 공식 API 확인 후 | 2단계 |
| `claude_logs` | 6 | `~/.claude/projects/*/*.jsonl` + git log. 판정 대상이 아니라 요약 대상이라 소스 플러그인이 아닌 `progress.py` 단계로 구현 (`profile.yaml`의 `progress:`, 화이트리스트) | 2단계 (구현 2026-10-27) |

## 5. 필터·요약 파이프라인

```
fetch → dedupe(seen ids) → prefilter(키워드/임베딩, 무료) → LLM judge(cheap 모델, 배치)
      → score ≥ threshold → LLM summarize(smart 모델, 5번 대상만) → digest JSON
```

- 판정 프롬프트에는 `profile.yaml`의 `research`(논문용), `notices`(공지·지원사업용), `career`(채용 필터)만 넣는다 (프롬프트 캐싱 대상).
  공지는 연구 주제가 아니라 `notices.interests`("내 상황에서 행동할 공지인가")로 판정한다. `notices`가 없으면 공지는 전부 탈락.
- 판정 결과는 SQLite에 저장 → 9번 피드백에서 "왜 이게 떴냐"에 `reason`으로 답할 수 있음.
- 사용자가 틀렸다고 표시한 판정은 `feedback` 테이블에 저장, 다음 판정 프롬프트에 few-shot으로 주입.

## 6. 진행 로그 (6번) 설계 메모

- 경로: `%USERPROFILE%\.claude\projects\<인코딩된 경로>\<session>.jsonl`
- **Claude Code는 오래된 세션 로그를 자동 정리한다** (`~/.claude/settings.json`의 `cleanupPeriodDays`, 기본 약 30일). 값을 늘리거나 매일 요약을 먼저 떠둘 것.
- 원본 통째 요약 금지. 추출 대상: 사용자 메시지, 편집된 파일 목록, 실행 명령, 그리고 같은 기간 `git log --stat`.
- 계층 요약: daily(세션→일) → weekly(일 7개→주) → monthly(주→월). 상위 요약은 하위 요약만 입력으로 씀.
- 선택: Claude Code `SessionEnd` hook으로 세션 종료 시 한 줄 요약을 바로 남기기.

## 7. 표시 (8번) / 피드백 (9번)

- 8번: `render/dashboard.py`가 digest JSON → 단일 `dashboard/index.html` 생성. Windows는 Lively Wallpaper에 이 파일 등록.
  JS로 주기적 reload. 클릭 시 원문 URL은 기본 브라우저로.
- 9번: 1차는 `daybrief-me` 폴더를 VS Code에서 열고 Claude Code로 대화 (`daybrief-me/CLAUDE.md`가 data/를 근거로 답하라고 지시).
  랩원 배포용으로는 이후 API 기반 간단 챗(트레이 앱 등) 추가.

## 8. 단계별 로드맵

0. **프로필 생성** (현재): `daybrief init-profile`. 로컬 근거로 `profile.proposed.yaml`, `journals.proposed.yaml` 생성 → 사용자 검토 후 병합.
   필터 품질이 전부 프로필에 달려 있으므로 이게 먼저다. 랩원 배포 시에도 첫 명령이 이것.
1. **논문 + 기관 뉴스 → 마크다운 다이제스트**. `openalex`, `html_list`, LLM judge, `render/markdown`. 1~2주 실사용하며 임계값·프롬프트 튜닝.
2. **진행 로그 + 채용**: `claude_logs` + 일/주/월 요약. 지원사업 Open API. 채용·산학 공고(§8.6).
3. **바탕화면**: HTML 대시보드 + Lively Wallpaper + 작업 스케줄러 등록 스크립트(`scripts/install_windows.ps1`).
4. **Notion·로드맵**: 연구실 페이지 변경 감지, 개인 Notion 로드맵에 승인형 반영.
5. **클라우드 이전 + 공개**: GitHub Actions 수집기, `daybrief init`, 랩원용 README.

## 8.5 프로필 생성 (0단계) 설계

수집 대상 (디스크 전체 스캔 금지, 지정 루트 + 알려진 위치만):
| 신호 | 위치 | 가중치 |
|---|---|---|
| Claude Code 사용자 메시지 | `~/.claude/projects/*/*.jsonl` (프로젝트당 최근 40개, 300자) | 높음: 실제로 하는 일 |
| git 커밋 제목, README/CLAUDE.md | `--roots` 아래 git 레포 | 높음 |
| Zotero 제목 + 게재지 | `~/Zotero/zotero.sqlite` (잠금 회피 위해 복사본 읽기) | 중간: 읽는 것. 게재지 빈도는 저널 후보의 근거 |
| PDF 첫 페이지, .bib 제목 | `--roots` 아래 | 중간 |

- 전송 전 시크릿·이메일 마스킹, 신호당 길이 제한, 총 6만 자 상한 (작업 신호 우선).
- `--preview`: LLM 호출 없이 전송될 내용을 파일로 저장 → 사람이 먼저 확인.
- LLM 출력의 각 주제는 `[S#]` 근거를 달아야 하고, 실제 존재하지 않는 근거만 가진 주제는 코드에서 버린다.
- `not_interested`는 로컬 파일로 추론 불가 → 항상 사용자가 직접 작성.
- 주기적 재생성(월 1회 등)으로 관심사 변화를 따라가되, 결과는 항상 제안 → 승인.

## 8.6 채용·산학 공고 설계

**방향: 연구 → 공고 매칭. 공고 → 연구 방향 수정이 아니다.**
기업 수요로 연구를 좁히지 않는다. 이미 하고 있는 연구(프로필의 근거)에서 공고 요구사항과 겹치는 지점이 있는지를 판단한다.

- 판정 출력은 "이 공고의 요구 X ↔ 내 연구의 Y (근거: 프로필 evidence)" 형태의 **매칭 근거 쌍**. 매칭 근거를 못 대면 올리지 않는다.
- 공고 요구 중 내 연구에 없는 것은 기록만 하고 **로드맵(7번)에 넣지 않는다.** 로드맵 제안의 입력은 오직 내 진행 기록뿐.
- `career`(시기, 직무, 조직 유형)는 사용자가 직접 작성. 필터 조건일 뿐 연구 판단에 쓰지 않는다.
- **회사 목록을 LLM이 상상으로 만들지 않는다.** 실제 공고 수집 → 매칭된 공고를 낸 회사 집계 →
  `companies.proposed.yaml`(매칭 공고 링크가 근거) → 승인 후 감시 대상에 추가.
- 출력:
  1. **일일**: 지금 행동 가능한 매칭 공고 (산학장학생, 인턴, 연구 과제, 마감 임박). `career.horizon` 기준.
  2. **월간**: "내 연구의 어떤 요소가 어떤 조직의 공고와 매칭됐나" 집계. 연구 → 수요처 방향의 지도이지, 역량 갭 목록이 아님.

### 수집 우선순위와 범위
1. 공식·공공 API (잡알리오 등 공공기관 채용 오픈API)
2. 감시 대상 기업의 **자체 채용 페이지**
3. 민간 채용 플랫폼(사람인 등): 공식 API가 확인될 때만. 플랫폼 데이터 크롤링은 국내 분쟁 사례가 있으므로 쓰지 않는다.

크롤링 운영 원칙: robots.txt·약관 준수, 하루 1회 저빈도, 개인 열람 목적.
**랩 배포 시에는 코드만 공유하고 수집 데이터는 공유하지 않는다** (각자 자기 기기·계정에서 실행). 수집물을 모아 재배포하는 순간 위험도가 크게 올라감.

### 첨부·이미지 공고 처리 (`pipeline/extract.py`, 2단계)
| 형식 | 방법 |
|---|---|
| HTML 본문 | selectolax |
| PDF | pypdf / pdfplumber (텍스트 레이어), 없으면 이미지로 처리 |
| HWPX | zip + XML 파싱 (표준 라이브러리로 충분) |
| HWP (5.0 바이너리) | `pyhwp`의 hwp5txt. 실패 시 LibreOffice headless 변환을 fallback |
| 이미지 공고 (통짜 JPG/PNG) | 비전 LLM으로 텍스트·요구사항 추출. 비용 때문에 제목·회사명 1차 판정을 통과한 공고에만 적용 |
추출 결과는 공고 URL과 함께 캐시하고, 추출 실패는 다이제스트에 "본문 미확인"으로 표시 (요약을 지어내지 않음).

## 8.7 논문 본문 접근 원칙

학교 구독은 **캠퍼스 IP·학교 프록시 로그인에 묶인 권한**이라 클라우드(Actions, LLM 서버)에서는 쓸 수 없고,
구독 계약상 자동 대량 다운로드나 제3자 서비스로의 본문 전달이 제한되는 경우가 많다. 그래서:

1. **판정·일일 요약은 초록 기준** (OpenAlex/출판사 메타데이터, 항상 합법적으로 접근 가능). 요약에 `basis: abstract` 표기.
2. **오픈액세스 본문**: OpenAlex의 `oa_url`(Unpaywall 계열)이 있으면 그걸로 본문 요약 (`basis: fulltext`).
3. **구독 논문**: 자동으로 받지 않는다. 다이제스트에 학교 도서관 프록시를 거친 "원문 열기" 링크만 붙인다
   (`profile.yaml`의 `library.proxy_prefix` + DOI 링크). 사람이 열어서 읽는다.
4. **사람이 받은 PDF**: 사용자가 `inbox/papers/`에 PDF를 넣으면 로컬(`run_on: local`)에서 본문 요약.
   출판사 이용 조건이 외부 AI 서비스 입력을 금하는지는 출판사별로 다르므로, 이 기능은 사용자가 켜는 opt-in.
5. 출판사 공식 TDM(텍스트 마이닝) API가 학교 권한으로 제공되면 그 경로는 검토 대상 (도서관에 확인 후).

## 9. 미결정 사항## 9. 미결정 사항 (구현 전 사용자 확인 필요)
- Q1 저널 목록 최종본 (`journals.proposed.yaml` 후보를 SJR에서 분야·연도별 사분위 확인)
- 기관 공지 게시판 URL과 셀렉터 (실제 페이지 구조 보고 결정)
- 연구실 Notion이 웹 게시 상태인지
- LLM 월 예산 상한
- 채용 소스: 잡알리오 오픈API 엔드포인트·필드 확인, 민간 플랫폼 공식 API 존재 여부 확인
- 경북대 도서관 프록시 URL 형식, 출판사 TDM API 이용 가능 여부 (도서관 문의)
