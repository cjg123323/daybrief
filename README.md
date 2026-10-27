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
- Lively 없이 보려면 `dashboard/index.html`을 브라우저로 열면 된다. 스타일만 바꿨을 때는 `daybrief render --config profile.yaml`로 수집 없이 재생성.

## 새 소스 추가
`src/daybrief/sources/<name>.py`에 `@register("<name>")` 클래스 하나 (`fetch(since, cfg) -> list[Item]`),
`sources/__init__.py`에 import 한 줄. 코어는 수정하지 않습니다.
