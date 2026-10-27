# daybrief — Claude Code 작업 지침

이 레포는 **공개 엔진**이다. 개인 설정·데이터는 별도 private 레포(`daybrief-me`)에 있고,
이 레포에는 절대 들어오지 않는다. 전체 설계는 `DESIGN.md`를 먼저 읽을 것.

## 무엇을 만드는가
매일 1회(또는 로그인 시) 다음을 수집·필터링·요약해 Windows 바탕화면에 띄우는 개인 브리핑 에이전트.
1. 소속 기관(경북대 등) 공지/뉴스
2. 거주 지역 지원사업·공고 (주거 지원 등)
3. 연구실 Notion 페이지 변경 감지
4. 관심 분야 Q1 저널 신규 논문
5. 본인 연구와 밀접한 논문의 개별 요약
6. Claude Code 세션 로그 → 프로젝트별 일/주/월 진행 요약
7. 진행 요약 기반 연구 로드맵 갱신 **제안** (승인 후 반영)
8. 바탕화면 GUI (Lively Wallpaper용 로컬 HTML)
9. 내 연구와 매칭되는 채용·산학 공고 (DESIGN.md §8.6)

## 절대 원칙 (위반하는 구현은 거절할 것)
1. **런타임은 Claude Code에 의존하지 않는다.** 순수 Python + LLM API. 랩원이 Claude Code 없이도 돌릴 수 있어야 한다.
2. **모든 Item은 원문 URL을 가진다.** URL 없는 요약은 생성하지 않는다. 요약에는 근거(초록/본문/로그 중 무엇) 출처를 표기.
3. **LLM은 판정과 요약만 한다.** 날짜, 저널명, 저자, DOI 같은 메타데이터는 반드시 원천 데이터에서 가져오고 LLM 출력으로 덮어쓰지 않는다.
4. **로드맵은 절대 자동으로 덮어쓰지 않는다.** 변경은 `RoadmapProposal`(근거 커밋/세션 링크 포함)로만 생성하고, 사용자가 승인해야 반영.
5. **개인정보는 엔진에 들어오지 않는다.** `profile.yaml`, `data/`, `.env`, 토큰, 로그 원본은 커밋 금지. 예시는 `examples/`에 가짜 값으로만.
6. **소스는 플러그인이다.** 새 수집원 추가 시 코어 수정 없이 `sources/`에 파일 하나 + 설정 항목 하나로 끝나야 한다.
7. **스크래퍼는 깨진다는 전제로 짠다.** 소스 하나가 실패해도 전체 파이프라인은 계속 돌고, 실패는 브리핑 상단에 표시한다.
8. **채용은 연구 → 공고 매칭이다.** 공고 요구사항으로 연구 방향이나 로드맵을 제안하지 않는다. 매칭 근거 쌍을 못 대면 올리지 않는다.
9. **구독 논문 본문을 자동으로 받지 않는다.** 초록 + OA 본문 + 사용자가 직접 넣은 PDF(opt-in)만. 수집 데이터는 재배포하지 않는다.

## 코드 규칙
- Python 3.11+, 타입 힌트 필수, `ruff` + `pytest`.
- 네트워크 호출은 전부 `httpx` + 타임아웃 + 재시도. 크롤링은 사이트당 요청 간격 ≥1초, robots.txt 존중.
- 외부 API 응답은 테스트에서 `tests/fixtures/`의 저장된 응답으로 대체 (네트워크 없는 테스트).
- LLM 호출은 `daybrief.llm.LLM` 인터페이스로만. 공급자 SDK를 다른 모듈에서 직접 import 금지.
- 비용 의식: 대량 필터링은 저가 모델(`models.cheap`), 정밀 요약만 상위 모델(`models.smart`). 프롬프트 캐싱 활용.
- Windows가 1차 타깃. 경로는 `pathlib`, 사용자 홈은 `Path.home()`.

## 작업 순서
`DESIGN.md`의 "단계별 로드맵"을 따른다. 현재 단계: **1단계 실사용 튜닝 중 + 3단계 바탕화면 HTML 선반영** (0단계 완료 2026-10-20, 3단계 렌더러 2026-10-27, Lively 등록은 사용자 몫).
개인 인스턴스의 진행 현황·결정 사항은 `../daybrief-me/STATUS.md`에 있다 (있으면 먼저 읽을 것).
연구 프로필을 대화 기억이나 추측으로 채우지 말 것. 반드시 init-profile 결과(근거 포함)를 사용자가 검토한 것만 쓴다.
다음 단계로 넘어가기 전에 사용자가 1~2주 실사용하며 필터 품질을 확인한다.

## 자주 쓰는 명령
```
pip install -e ".[dev,anthropic,profile]"
daybrief init-profile --roots D:\research --out-dir ../daybrief-me --privacy ../daybrief-me/init_profile_privacy.yaml --preview   # 전송 내용 먼저 확인
daybrief init-profile --roots D:\research --out-dir ../daybrief-me --privacy ../daybrief-me/init_profile_privacy.yaml
daybrief init-journals --roots D:\research --out-dir ../daybrief-me --privacy ../daybrief-me/init_profile_privacy.yaml   # Zotero 없을 때 PDF→OpenAlex로 저널 후보
daybrief run --config ../daybrief-me/profile.yaml       # 전체 수집 + 다이제스트
daybrief run --config ... --only openalex --dry-run     # 소스 하나만, LLM 호출 없이
pytest
```
