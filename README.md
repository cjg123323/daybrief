# daybrief

연구자용 개인 일일 브리핑 에이전트. 관심 분야 저널 신규 논문, 소속 기관 공지, 지역 지원사업 공고를
매일 수집하고 LLM으로 본인 연구 기준 필터링·요약해서 보여줍니다.

> 개발 중 (0단계 프로필 → 1단계 논문·공지 다이제스트 실사용 튜닝 → 3단계 바탕화면 HTML까지 동작). 설계는 [DESIGN.md](DESIGN.md).

## 구조
- **이 레포 (공개)**: 엔진. 수정·기여는 여기로.
- **본인 private 레포**: `profile.yaml`, `journals.yaml`, 수집 결과. 엔진을 의존성으로 설치해서 씀.

## 빠른 시작
```bash
pip install "daybrief[anthropic,profile] @ git+https://github.com/cjg123323/daybrief"
# private 레포에서
cp <engine>/examples/profile.example.yaml profile.yaml
daybrief init-profile --roots <연구 폴더> --preview      # LLM에 보낼 내용 확인 (마스킹 후)
daybrief init-profile --roots <연구 폴더>               # profile.proposed.yaml, journals.proposed.yaml 생성
# → 근거 확인 후 profile.yaml / journals.yaml로 옮기기
daybrief resolve-journals journals.yaml --mailto you@example.com
daybrief run --config profile.yaml --dry-run              # LLM 없이 수집만 확인
set ANTHROPIC_API_KEY=...                                 # Windows
daybrief run --config profile.yaml
```
결과: `data/digest/YYYY-MM-DD.md`, `.json`, 그리고 바탕화면용 `dashboard/index.html`

## 바탕화면에 띄우기 (Windows)
1. 작업 스케줄러 등록 (엔진 레포 루트에서, 로그온 시 + 매일 07:00에 `daybrief run`):
   `powershell -ExecutionPolicy Bypass -File scripts/install_windows.ps1 -Config ../<private>/profile.yaml`
2. [Lively Wallpaper](https://github.com/rocksdanister/lively) 설치: `winget install rocksdanister.LivelyWallpaper` (또는 Microsoft Store). 한 번 실행해 둔다.
3. 라이브러리에 **원본 링크**로 등록 (엔진 레포 루트에서):
   `powershell -ExecutionPolicy Bypass -File scripts/install_lively.ps1 -Config ../<private>/profile.yaml`
   그 다음 Lively를 완전히 종료(트레이 → Exit)하고 다시 실행 → 라이브러리에서 **Daybrief** 선택.
   **주의:** Lively의 "+ 추가 → 파일 선택"으로 넣으면 HTML이 Lively 폴더에 *복사*되어 매일 갱신이 바탕화면에 반영되지 않는다. 반드시 스크립트로 등록할 것.
   파일 경로는 등록 시점에 고정되므로 레포를 옮기면 스크립트를 다시 실행한다.
4. 카드를 클릭하면 원문이 기본 브라우저에서 열린다 (Lively 설정에서 "배경화면 입력"이 켜져 있어야 함, 기본값 켜짐).
   페이지는 10분마다 스스로 새로고침하고, `daybrief run`이 끝날 때마다 파일이 갱신된다.
5. 바탕화면 조작: Lively는 **마우스 휠을 전달하지 않으므로** 열 오른쪽 ▲▼ 버튼·막대 클릭·손잡이 드래그로 스크롤한다.
   섹션당 `dashboard.max_per_section`개만 보이고, "외 n건 더 보기" / 상단 "전체 펼치기"로 숨긴 카드와 잘린 글을 그 자리에서 펼친다.
- **첫 클릭이 먹히지 않는 경우:** Lively는 맨 앞 창이 바탕화면일 때만 클릭을 넘긴다. 링크로 브라우저가 열린 뒤
  바탕화면을 누르면 첫 클릭은 바탕화면 활성화에만 쓰인다. `Win+D`로 바탕화면으로 돌아오면 바로 한 번에 눌린다.
- 바탕화면에서 `file://` 링크는 열리지 않는다 (Lively가 `https://<hash>.localhost/`로 페이지를 연다).
  모든 항목을 브라우저에서 보려면 탐색기에서 `dashboard/full.html`을 직접 연다.
- Lively 없이 보려면 `dashboard/full.html`(또는 `index.html`)을 브라우저로 열면 된다. 스타일만 바꿨을 때는 `daybrief render --config profile.yaml`로 수집 없이 재생성.

## 프로젝트 진행 요약 (Claude Code 로그, 로컬 전용)
`profile.yaml`의 `progress.projects`에 **요약할 폴더만** 적는다 (적지 않은 폴더의 대화는 읽지 않음).
`daybrief run`이 끝난 날마다 일간, 끝난 주·달마다 주간·월간 요약을 `data/progress/<프로젝트>/`에 만든다.
LLM에는 마스킹(실명·키·이메일·홈 경로)·절단된 요청/편집 파일/명령/커밋만 가고, 원본 로그는 기기 밖으로 나가지 않는다.
```bash
daybrief progress --config profile.yaml --preview   # LLM 없이: 요약 대상 날짜와 보낼 내용(data/logs/progress_preview.txt) 확인
```
Claude Code는 오래된 로그를 지우므로 `~/.claude/settings.json`에 `"cleanupPeriodDays": 365`를 권장.

## 정부·지자체 지원사업 (보조금24)
1. [data.go.kr](https://www.data.go.kr/data/15113968/openapi.do) "행정안전부_대한민국 공공서비스(혜택) 정보" 활용신청 (자동승인).
2. 마이페이지의 일반 인증키(Decoding)를 **환경변수**로: `[Environment]::SetEnvironmentVariable("DATA_GO_KR_KEY", "<키>", "User")` (파일에 넣지 말 것).
3. `profile.yaml`의 `sources.gov24`에 지역(`agency_prefixes: [대구광역시]` 처럼 시·도 전체 이름), 분야, 제외 용어를 적고,
   `notices.situation`에 나이·거주 형태 등 자격 판단에 필요한 상황을 적는다. 카탈로그라서 첫 실행에만 전체가 판정되고 이후엔 새 서비스·새 회차만 올라온다.

## 새 소스 추가
`src/daybrief/sources/<name>.py`에 `@register("<name>")` 클래스 하나 (`fetch(since, cfg) -> list[Item]`),
`sources/__init__.py`에 import 한 줄. 코어는 수정하지 않습니다.
