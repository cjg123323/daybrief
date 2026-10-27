# 플러그인 모듈을 import하면 @register로 레지스트리에 등록된다.
# 새 소스를 추가하면 여기에 한 줄 추가.
from daybrief.sources import gov24, html_list, openalex  # noqa: F401
