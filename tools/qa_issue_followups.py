"""Static regression QA for the open-issue follow-up patch.

This checks the reported scenario and battle records, their repeated copies,
the executable pools used by the status and spirit-command windows, and the
absence of superseded terminology.  It never starts an emulator.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work" / "text_extracted" / "ssrw_japanese_text.json"
TRANSLATION = ROOT / "data" / "full_translation_ko.json"
EXPECTED_COUNTS = {"scenario": 8427, "battle": 5355, "menu": 28}


SCENARIO_EXPECTED = {
    "SCE-058-0118": "브라이트「과연 잔스칼이다.\n 쉽게는, 본국에 접근시켜 주지\n 않는군. 이대로 싸워도 승산은 없다.\n 철수 신호를 내려라」",
    "SCE-058-0111": "코우지「보스, 역시 보스보로트는\n 나랑 안 맞네.\n 마징가Z를 돌려줘」",
    "SCE-058-0113": "코우지「알겠다고. 보스는 대단해\n 저런 엉터리 로봇을\n 움직일 수 있으니까」",
    "SCE-058-0114": "보스「엉터리라니, 뭐가 엉터리냐!\n 열받는군!」",
    "SCE-058-0116": "보스「그렇지.\n 낡긴 했어도.. 응?\n 누케! 어디가 낡았다는 거냐!」",
    "SCE-058-0057": "라이「류세이, 너 여기까지 어떻게 온 거냐?」",
    "SCE-058-0059": "웃소「에-엣, 그런 게 가능한 건가요?」",
    "SCE-058-0060": "라이「웃소, 이런 녀석 말을\n 그냥 그대로 믿으면 안 된다.\n 이 녀석은 엄청난 거짓말쟁이야」",
    "SCE-058-0061": "류세이「아무렇지 않게 말하는군, 라이.\n 엄청난 거짓말쟁이라니 무슨 소리야!」",
    "SCE-058-0063": "류세이「어, 하하하하.\n 뭐, 조금은 했지만..\n 그래도 엄청난 거짓말쟁이는\n 아니잖아」",
    "SCE-058-0064": "웃소(어떤 사람일까, 이 사람)",
    "SCE-058-0069": "코우지「소개 같은 건 필요 없어」",
    "SCE-058-0071": "류세이「어랏, 화내고 가 버렸네\n 내가 뭔가 한 건가」",
    "SCE-058-0074": "하야토「예, 우리 겟타 팀과\n R-1의 류세이 다테 소위는\n 오늘부터 함장님의 지휘 아래로\n 배속되었습니다.」",
    "SCE-058-0075": "브라이트「도움은 되겠군.\n 전력은 언제든지 필요하네」",
    "SCE-058-0078": "라이「진 겟타!?\n 그, 그럼 드디어 봉인을 푼 겁니까?」",
    "SCE-058-0086": "오델로「뭐라고!?\n 그럼 샤크티가 루페 시노를\n 놓아줬다는 거야?」",
    "SCE-058-0087": "수지「응, 스테이션에 가까워졌을 때\n 엄마 목소리가 들린다고.\n 나는 말렸어. 그런데..」",
    "SCE-058-0089": "워렌「울지 마, 수지」",
    "SCE-058-0093": "브라이트「이제 와서 데려오는 건 무리다.\n 어쩔 수가 없다」",
    "SCE-058-0094": "웃소「샤크티를 두고 가자고요?\n 그건, 저는 싫어요!」",
    "SCE-058-0095": "마베트「웃소 군, 샤크티는 군인이 아니야.\n 걱정하지 않아도 괜찮아」",
    "SCE-058-0097": "올리퍼「마베트 말이 맞다.\n 게다가, 어떻게 잔스칼에\n 갈 건데?」",
    "SCE-058-0099": "올리퍼「그렇게 했다간 잔스칼에\n 가기는커녕 다가가기만 해도\n 벌집이 되고 만다」",
    "SCE-058-0121": "하야토「코우지 군, 유미 교수에게서\n 마징가Z의 개조용 파츠를 받아 왔다.\n 정비사를 불러와 주지 않겠나」",
    "SCE-058-0124": "하야토「음, 카부토 쥬조 박사가\n 남기신 개발 노트의 아이디어로\n 만들어졌다더군」",
    "SCE-058-0127": "하야토「그래, 그 상자 안의 것을\n 마징가Z에 조립해 넣어 주게.\n 설명서도 들어 있을 거다」",
    "SCE-059-0029": "샤아「그렇다. 이성인의 지식과\n 기술을 흡수하려면, 시간이 좀 더\n 필요하다.<WAIT> 그때까지 사람들이 얌전히\n 있어 줬으면 좋겠는데.\n 알겠나, 나나이」",
    "SCE-059-0033": "샤아「곁에 있어 주지 않으면\n 곤란해, 나나이」",
    "SCE-059-0045": "브라이트「아뇨, 대단한 건\n 아닙니다... 알겠습니다.\n 사이드5를 조사해 보겠습니다」",
    "SCE-059-0046": "자하남「뭐, 가 준다고!?\n 그렇군, 그렇군, 와하하하하!\n 그럼, 잘 부탁하네!」",
    "SCE-059-0048": "크로노클「나를 우습게 만든 놈들은\n 그리 쉽게 놓아주지 않겠다.<WAIT> 카테지나,\n 모빌슈트 대의 지휘를 맡아라!」",
    "SCE-059-0051": "올리퍼「크로노클이군!\n 어떻게 이리도 끈질긴 놈이지」",
    "SCE-059-0098": "카테지나「너는 네 생각만으로\n 너무 앞서 나가서 주위를 못 봐」",
    "SCE-060-0004": "샤아「나는, 네오 지온의 샤아다.<WAIT> 지금부터 이야기할 것을 잘 들어\n 주기 바란다.\n 전 인류의 미래가 걸린 일이다」",
    "SCE-060-0005": "네스「샤아입니다. 샤아가 일제히 방송을\n 송출하며 이야기하고 있습니다」",
    "SCE-060-0008": "아무로「말도 안 돼! 그건 완전히\n 노예나 다름없잖아!」",
    "SCE-060-0009": "코우지「이런 놈은 살려 둘 수 없겠는데.\n 그렇지, 보스.」",
    "SCE-060-0015": "올리퍼「저기가 사이드5다」",
    "SCE-060-0027": "아야「저와 라이가 갈게요.\n 괜찮지, 라이?」",
    "SCE-060-0031": "오델로「알고 있다니까, 운석이잖아.\n 너야말로, 더미를 흩뜨리지 마라」",
    "SCE-060-0042": "아무로「오델로, 저쪽으로 접근해 줘」",
    "SCE-060-0045": "아무로「오델로, 우리가 놈들의\n 주의를 끌 테니, 화이트 아크는\n 최대한 멀리 떨어져서 라 카이람에\n 연락해라」",
    "SCE-060-0060": "닥터 헬「실험은 지금이 중요한 때다.\n 무슨 짓을 해서라도, 놈들을\n 이곳에 접근시키지 마라!」",
    "SCE-060-0061": "젝스「핫!」\n (언제 봐도 기분 나쁜 놈들이군.\n 여기서 대체 무슨 실험을\n 하고 있는 거지?)",
    "SCE-060-0063": "닥터 헬「너는 여기를 맡아 지켜라.\n 고양이 새끼 한 마리도\n 들여보내지 마라!」",
    "SCE-060-0085": "히이로「....서툴군」",
    "SCE-060-0088": "젝스「이 톨기스\n 아직 조정이 서툴군」",
    "SCE-062-0007": "류세이「합체!? 아, 아직 위험한데-\n 지금까지 성공한 적이 없어서」",
    "SCE-062-0034": "아무로「진 소령, 아야의 안색이 안 좋아 보이는데,\n 오늘은 쉬게 해 주시죠」",
    "SCE-062-0036": "라이「SRX 합체 시 쓰는 아야의\n 염동력은 상당한 것입니다.\n 지치는 것도 무리가 아닙니다」",
    "SCE-062-0046": "코우지「이봐, 보스! 따라오지 말라고!」",
    "SCE-062-0047": "보스「흥, 네 녀석이\n 보로트를 따라온 거잖아.\n 짐이나 되지 마라!」",
    "SCE-062-0048": "코우지「쳇, 말은 잘하네!」",
    "SCE-063-0004": "나나이「사이드5의 기지가\n 파괴되었다는 연락이\n 들어왔습니다」",
    "SCE-063-0094": "카테지나「너는 네 생각만으로\n 너무 앞서 나가서 주위를 못 봐」",
}


BATTLE_EXPECTED = {
    "BTT-03-10-001A74": "「탄막이 얇다!! 뭘 하고 있나!!」",
    "BTT-03-19-001B0B": "「그렇게 두지 않겠다!!」",
    "BTT-03-1E-001B73": "「좌현 탄막이 얇다! 뭘 하고 있나!?」",
    "BTT-13-0D-005ABC": "「너희 따윈-!!」",
    "BTT-18-06-00794F": "「너희 따윈-!!」",
    "BTT-39-14-0171EB": "「뭐.. 마, 말도 안 돼..!?」",
    "BTT-E1-14-0471EB": "「뭐.. 마, 말도 안 돼..!?」",
    "BTT-55-32-0205AE": "「소용없다!\n 소용없어어어어!!」",
    "BTT-DE-04-045942": "「미라쥬 드릴!!」",
    "BTT-DF-04-046130": "「대설산 던지기\n 2단 뒤집기다-!!」",
    "BTT-F4-1F-04FD90": "「이걸로 끝내자고..\n 천상천하아앗..」",
}


POOL_EXPECTED = {
    "PILOTFULL": {
        "ブロッホ": "브롯흐",
        "ゾンビ兵": "좀비병",
        "兵士": "병사",
    },
    "PILOTNAME": {
        "ブロッホ": "브롯흐",
        "ゾンビ兵": "좀비병",
        "兵士": "병사",
    },
    "UNITNAME": {
        "ドッゴ-ラ": "돗고라",
        "ブルッケング": "브루켕",
        "ブルッケング(KM)": "브루켕(KM)",
    },
    "WEAPON": {
        "ミラ-ジュドリル": "미라쥬 드릴",
        "大雪山おろしニ段返し": "대설산 던지기 2단 뒤집기",
    },
    "SYS": {
        "エンジェル.ハイロゥ": "엔젤 하일로",
        "エンジェルハイロゥ": "엔젤 하일로",
        "空水": "공해",
    },
}


MENTAL_COMMANDS = {
    1: "근성",
    2: "대근성",
    3: "보급",
    4: "우정",
    5: "신뢰",
    6: "사랑",
    7: "격노",
    8: "기합",
    9: "가속",
    10: "열혈",
    11: "필중",
    12: "번뜩임",
    13: "행운",
    14: "각성",
    15: "위압",
    16: "봐주기",
    17: "집중",
    18: "격려",
    19: "재동",
    20: "부활",
    21: "은신",
    22: "탈력",
    23: "자폭",
    24: "탐색",
    25: "족쇄",
    26: "교란",
    27: "정찰",
    28: "철벽",
    29: "혼",
    30: "기적",
    31: "간파",
}


# These are the records touched by the current open-issue pass.  Keeping
# their exact text here prevents a future rebuild from silently reverting a
# screenshot correction while the full source-ID/count check protects every
# other scenario record.
ISSUE_SCENARIO_EXPECTED = {
    "SCE-008-0082": "레인「도몬, 기다려....\n 앗, 안녕히 계세요.\n 도몬, 도몬이라니까!」",
    "SCE-008-0089": "사콘「호오...\n 그가, 그 유명한 킹 오브\n 하트인가. 그런 거 치곤 젊군」",
    "SCE-009-0004": "다이몬지 박사「유럽의 상황은....\n ..긴박한 이유로, 서둘러 손을\n 써야겠다고 판단해, 그들을 남겨\n 두고 왔습니다」",
    "SCE-009-0006": "다이몬지 박사「예, 하지만, 이번엔,\n 저희 쪽이 허술해지고 맙니다」",
    "SCE-009-0007": "오카 장관「걱정할 것 없네. 이미,\n 대공마룡대 보강을 위한 새 멤버를\n 모으고 있네. 소개하지」",
    "SCE-009-0025": "오카 장관「경찰도 범인 체포에\n 전력을 다하고 있네만, 단서조차\n 잡지 못하고 있네. 제군들도, 주변을\n 충분히 경계하도록」",
    "SCE-009-0044": "아키라「저 녀석은, 슈퍼로봇\n 매니아야. 좀 징그럽긴 해도, 악의는\n 없어. 너무 신경 쓰지 않는 게\n 좋아」",
    "SCE-009-0050": "류세이「R-1은 리얼로봇 계열이라\n 소형이거든. 브라○가처럼\n 거대화라도 하면 재미있을 텐데」",
    "SCE-009-0051": "산시로「뭘 알아들을 수도 없는\n 소리를 하고 있는 거냐.\n 적들에게 격추당한다!」",
    "SCE-009-0056": "피트「누구랑 마찬가지로, 열혈\n 외길을 걷는 건 틀림없지」",
    "SCE-010-0035": "다이몬지 박사「사람을 납치했다고?\n 사콘 군, 자세하게 조사해 주게」",
    "SCE-010-0066": "미도리「쿨한 느낌이 멋지네.\n 그렇지, 이쿠에 양」",
    "SCE-011-0009": "오카 장관「음, 대신 극동지부 소속\n 수전기대에게, 유럽으로 가게 할\n 생각이네」",
    "SCE-011-0010": "류세이「수전기대?\n 그거 단쿠가잖아.<WAIT> 알아? 수전기대에는 어그레시브\n 모드라는 게 있어서 말이야...」",
    "SCE-011-0011": "사콘「수전기대라... 또 다시,\n 문제아들이군」",
    "SCE-011-0018": "다이몬지 박사「아니요, 신경 쓰지\n 마십시오.\n 그런데, 무엇을 운반하면\n 되겠습니까?」",
    "SCE-011-0043": "사콘「수리는 필요하지만, 큰 피해는\n 아닌듯 싶습니다」",
    "SCE-011-0044": "다이몬지 박사「그런가.\n 교수님, 들으신 대로입니다.<WAIT> 폐가 되지 않도록, 수리가\n 끝날 때까지 잠시 자리를\n 빌리겠습니다」",
    "SCE-011-0050": "산시로「어이 이봐, 너무 내 옆에\n 붙지 마라」",
    "SCE-011-0055": "라이「무슨 소리를 한 거냐? 류세이?」",
    "SCE-012-0006": "유미 교수「해저화산치고는 조금\n 부자연스럽습니다.\n 인공적인 것이 아닐까 하고..」",
    "SCE-012-0009": "다이몬지 박사「이성인의 기지가,\n 오가사와라 제도에 있다는\n 말씀입니까?」",
    "SCE-012-0010": "유미 교수「그럴 가능성이 높지\n 않을까 생각합니다.」",
    "SCE-012-0029": "아키라「적당히 좀 해요!\n 정말이지」",
    "SCE-013-0007": "왓타「엄마, 이래저래 저금하고\n 있었으니까」",
    "SCE-013-0008": "카키코지「다이몬지 박사님, 참으로\n 죄송합니다만, 저희는 일단 도쿄로\n 돌아가겠습니다」",
    "SCE-013-0040": "히이로「뭘 하고 있는 거냐, 나는..\n 이 녀석이 죽어 주는 편이 나을\n 텐데」",
    "SCE-014-0005": "사콘「그렇군요.\n 가이킹의 조종도 함께 해\n 버리겠습니다」",
    "SCE-014-0015": "다이몬지 박사「대뜸 미안하네만,\n 사야카 양과 함께 제트 스크랜더를\n 가지고 유럽으로 가주게」",
    "SCE-014-0090": "동방불패「아니... 내가 인정한\n 킹 오브 하트인가!」",
    "SCE-014-0111": "도몬「함부로 내 이름을 부르지 마라」",
    "SCE-014-0112": "레인「도몬!\n 미안해요, 류세이 씨.<WAIT> 이 사람, 겉보기엔 무뚝뚝해도\n 속은 좋은 사람이에요」",
    "SCE-014-0113": "라이「류세이는 겉보기엔 바보인데\n 속도 바보지」",
    "SCE-014-0114": "류세이「라이!! 이 자식, 더는 못 참아!\n 앗, 서라!」<WAIT> 우당탕",
    "SCE-014-0117": "장갈「넵, 그것이 도무지 알 수\n 없는 노릇이옵니다」",
    "SCE-014-0127": "동방불패「도몬, 이 잔챙이들을\n 후딱 처치해 버리자꾸나!」",
    "SCE-015-0097": "다이몬지 박사「가이킹을 발진시켜라」",
    "SCE-037-0049": "동방불패「도몬.., 유파, 동방불패\n 최종오의, 석파천경권!\n 확실히 전수했노라!」",
    "SCE-037-0063": "동방불패「....도몬,<WAIT> ...나는, 너에게 사죄해야만 한다.\n 나는 지구권에 와서, 두 사람의\n 인간과 접촉을 가졌다.\n 하나는 네 아버지, 캇슈 박사다.\n 그리고, 또 한 사람의 남자\n 샤아 아즈나블.\n 지금 생각하면, 그자와 만난 것이\n 문제였다.\n 그자를 알게 된 때가 나빴던\n 것이다.<WAIT> 그것은, 지구와 콜로니가 몇\n 번이고, 치열한 전투를 되풀이한\n 뒤,<WAIT> 루나 조약에 의해 간신히 평화가\n 만들어졌을 때였다.\n 루나 조약, 너도 알다시피,\n 지구.달.각 콜로니군이,<WAIT> 서로를 독립국가로 인정하고,\n 서로에게 일체의 간섭을 하지\n 않는다는 진부한 타협안이다.\n 허나, 싸움에 지쳐 버린 인간들에게\n 는, 단순하기에 오히려\n 유효했다.\n 평화로워졌다고는 해도, 전쟁의\n 영향은 곳곳에 짙게 남아 있었다.<WAIT> 그자도, 지난 전쟁에서 많은 부하와\n 백성을 희생 시키고, 또, 그자\n 자신이 수많은 목숨을 앗아, 마음은\n 황폐해지고 깊이 상처 입었을\n 것이다<WAIT> 누구보다 인간을 사랑하는 마음을\n 지니 면서도, 그 이상으로 인간을\n 미워하고 있었다. 확실히, 시기가\n 너무 나빴다. 하지만, 여기 도착한\n 지 얼마 되지 않은 나로서는 그런\n 것을 알 리 없었다.<WAIT> 그자는, 지도자로서의 자질이\n 뛰어나고, 냉정침착하고 사려 깊은\n 태도와, 폭넓은 식견을 지니고\n 있었다.<WAIT> 그것에 현혹되고 만 것이다.\n 나는, 지구인의 대표로서 그자를\n 이해하려 했다.\n 그 결과, 나는 지구인이란\n 불안정하고 파괴를 즐기는 호전적\n 종족이며,<WAIT> 우리에게, 장래 위험 인자가 될 수\n 있는 존재라고 인식하고 말았다.<WAIT> 그리고, 최악의 사태에 대비할\n 필요가 생겨 나는 네 아버지 캇슈\n 박사를 이용한 것이다」",
    "SCE-063-0056": "아무로「웃소, 샤크티를 찾을 수\n 있을지 어떨지는, 알 수 없다.\n 각오는 해야 된다!」",
    "SCE-064-0010": "샤아「어디까지 내 일을 방해할 셈이냐\n 아무로....\n 그렇다면 여기서 결판을 내 주지」",
    "SCE-064-0013": "나나이「대령님, 이제는 말리지 않겠습니다만,\n 아무로를 쓰러뜨리면....?」",
    "SCE-064-0016": "샤아「착하군」",
    "SCE-064-0017": "코우지「우와-, 이렇게 거대한 걸,\n 용케도 만들었군.\n 그렇지, 후지와라」",
    "SCE-064-0018": "시노부「윽...\n 몇 번을 말해야 알아듣겠냐.\n 시노부다, 시.노.부」",
    "SCE-064-0019": "코우지「일일이 신경 쓰지 말라고」",
    "SCE-064-0020": "시노부「신경 쓰이니까 그렇게 말해 주는 거다」",
    "SCE-064-0021": "하야토「모두, 조심해라.\n 어디서 공격해 올지 모른다」",
    "SCE-064-0024": "아무로「그리고 여기다. 알겠나」",
    "SCE-064-0030": "류세이「그 말은,\n 다 같이는 갈 수 없다는 얘기군요」",
    "SCE-064-0031": "하야토「그렇다」",
    "SCE-064-0032": "마헤리아「우리는 소중한 공주님이야.\n 확실히 지켜 줘」",
    "SCE-064-0033": "라이「상당히 얄미운\n 공주님이군」",
    "SCE-064-0036": "헬렌「공주님이라....\n 가끔은 괜찮네, 보호받는 것도」",
    "SCE-064-0052": "료마「말리지 마.\n 끝까지 하게 놔둬.\n 때로는 레크리에이션도 필요하지」",
    "SCE-064-0069": "라이「우와아, 기, 기다려, 난 아니야..\n 윽.... 잘도 그랬겠다!」",
    "SCE-064-0076": "브라이트「....알겠다.\n 류세이, 시노부, 코우지, 앞으로\n 나와! 다리에 힘주고 이를 악물어라!」",
    "SCE-064-0079": "시노부「쳇, 제대로 걸렸군...」",
    "SCE-064-0080": "코우지「...어째서, 나까지\n 기합을 받는 거야」",
    "SCE-064-0087": "아무로「이런, 자, 잠깐...\n 우리가 싸우면 체면이 안 서잖나」",
    "SCE-064-0133": "젝스「후후, 너라면\n 좋은 승부를 할 수 있겠군.\n 전사에게는, 좋은 라이벌이 필요한\n 법이다」",
    "SCE-064-0151": "샤아「음, 제법이군, 아무로.\n 하지만 이 정도 대미지로는,\n 사자비는 떨어지지 않는다」",
    "SCE-064-0152": "아무로「뭐야!? 안 먹힌 건가?」",
    "SCE-065-0009": "로메로「레지스탕스의 리더가 되면\n 표적이 되기 쉽지.\n 그가 진짜 진 자하남일세」",
    "SCE-065-0011": "한겔그「웃소, 많이 컸구나.\n 너 혼자 지구에 남겨 두고 온 건\n 늘 마음에 걸렸다」",
    "SCE-065-0015": "한겔그「아뇨,\n 저야말로 실례했습니다.\n 그런데, 용건이란?」",
    "SCE-065-0016": "브라이트「혹시, 이성인 모함의\n 현재 위치를 파악하고 계십니까」",
    "SCE-065-0017": "한겔그「알 수 있을 겁니다.\n 곧 조사해서 연락하도록\n 하겠습니다」",
    "SCE-065-0028": "파라「그래, 샤아 대신 타시로가\n 인류를 지배하는 거야.\n 그러고 싶었잖아」",
    "SCE-065-0050": "코우지「아무것도. 그렇지, 보스」",
    "SCE-065-0052": "네스「우현에서 열원 접근 중!」",
    "SCE-065-0056": "네스「우측 동력부 피탄!\n 출력이 계속 저하되고 있습니다」",
    "SCE-065-0057": "브라이트「각 기 출격!\n 전원 본함을 지켜라!!」",
    "SCE-065-0059": "류세이「지구인 따위에게 민망한 꼴을\n 보였습니다. 송구합니다」",
    "SCE-065-0076": "크로노클「예, 알겠습니다.\n 피피니덴, 들었겠지.\n 이번엔 후퇴하지 마라!\n 후퇴하면 내가 쏘겠다!!\n 알겠나!」",
    "SCE-065-0078": "피피니덴「크로노클 이놈..\n 전술로 후퇴한 것을\n 도망친 것처럼 하다니..\n 젠장!\n 루페 시노!\n 잘 들어라, 여기가 마지막이다」",
    "SCE-065-0090": "쿠프「함장님!\n 응급 처치가 끝났습니다!\n 어떻게든 움직일 겁니다!」",
    "SCE-065-0112": "라이「그래」",
    "SCE-065-0156": "카테지나「나를 좋아하지, 웃소..\n 계속 사랑하고 있었던 거지..」",
    "SCE-065-0181": "파라「저게 하얀 놈이라면\n 여기서 질긴 인연을 끊겠다!!」",
    "SCE-065-0182": "파라「이 잔넥을 본 자는\n 모두 죽는 거야\n 아핫하하하하하하!」",
    "SCE-066-0010": "곳초「샤아도 당했군.\n 루 카인, 상처는 어떠한가」",
    "SCE-066-0011": "루 카인「예, 이제 괜찮습니다.\n 지구인 따위에게 민망한 꼴을\n 보였습니다. 송구합니다」",
    "SCE-066-0045": "샤아「나나이, 사내들 싸움에\n 끼어들지 마라!」",
    "SCE-066-0069": "샤아「목숨이 아까웠다면 네놈에게\n 사이코 프레임 정보 따위 줄\n 성싶으냐!」",
    "SCE-066-0071": "샤아「한심한 모빌슈트와 싸워 봤자\n 이기는 데 의미가 있나!」",
    "SCE-066-0072": "아무로「바보 취급하긴....!\n 그렇게 네놈은 영원히 타인을\n 내려다보는 것밖에 못 하는 거냐!」",
    "SCE-067-0038": "브라이트「그럼 좋다.\n 지금부터 작전회의에\n 들어간다」",
    "SCE-067-0045": "코우지「알겠습니다.\n 바베큐가 되고 싶진 않아요」",
    "SCE-067-0046": "곳초「용케 여기까지 왔군.\n 하지만, 이걸로 끝이다.\n 모두 죽어 주어야겠다」",
    "SCE-067-0053": "코우지「그야, 기쁜 게 당연\n 하죠.<WAIT> 하지만, 어쩐지, 이렇게.....\n 말로 표현을 못 하겠네」",
    "SCE-067-0062": "젝스「나는 젝스 마키스!\n 싸울 의향은 없다!」",
    "SCE-067-0066": "젝스「샤아에게서 메시지를 맡아 두었다.\n 받아 주었으면 한다」",
    "SCE-067-0075": "그레스코「예, 이쪽 상황이\n 좋지 않다는 연락을 받았기에」",
    "SCE-067-0081": "그레스코「카를라! 기우라!\n 너희는, 즉시 출격해라」",
    "SCE-067-0083": "기우라! 핫!」",
    "SCE-067-0084": "곳초「뭐라, 그레스코가 격침됐다고!?\n 윽..... 놀이는 끝이다!!」",
    "SCE-067-0088": "곳초「너는?... 파라<WAIT> 이 내가 지구인인 네 도움을\n 빌린다고?\n 후후후후, 묘하군...」",
    "SCE-067-0090": "곳초「이상한 여자군, 너는.\n 좋다!\n 네 도움을 받도록 하지」",
}


ISSUE_BATTLE_EXPECTED = {
    "BTT-13-0B-005A96": "「떨어져라! 떨어져!!」",
}


TITLE_EXPECTED = {
    "ベスパと異星人": "『베스파와 이성인』",
    "エンジェル・ハイロゥ": "『엔젤 하일로』",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def all_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from all_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_strings(child)


def terminated(data: bytes, offset: int, limit: int) -> bytes | None:
    cursor = offset
    while cursor < min(limit, len(data)):
        opcode = data[cursor]
        if opcode == 0xFF:
            return data[offset:cursor + 1]
        if 0xF0 <= opcode <= 0xF5:
            cursor += 2
        elif opcode >= 0xF6:
            # These are the renderer controls used by the executable pools.
            cursor += 1 + {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1,
                            0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 1,
                            0xFE: 2}.get(opcode, 0)
        else:
            cursor += 1
    return None


def source_ids(source: dict[str, Any], section: str) -> set[str]:
    if section == "scenario":
        return {
            row["id"]
            for scenario in source[section]["scenarios"]
            for row in scenario["records"]
            if row.get("japanese")
        }
    return {row["id"] for row in source[section]["records"] if row.get("japanese")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, help="built image directory to inspect")
    args = parser.parse_args()

    source = read_json(SOURCE)
    full = read_json(TRANSLATION)
    errors: list[str] = []
    counts = {section: len(full[section]) for section in EXPECTED_COUNTS}
    if counts != EXPECTED_COUNTS:
        errors.append(f"translation counts {counts} != {EXPECTED_COUNTS}")
    for section in ("scenario", "battle"):
        if set(full[section]) != source_ids(source, section):
            errors.append(f"{section} translation IDs do not match extracted source")

    for record_id, expected in SCENARIO_EXPECTED.items():
        if full["scenario"].get(record_id) != expected:
            errors.append(f"scenario mismatch at {record_id}")
    for record_id, expected in ISSUE_SCENARIO_EXPECTED.items():
        if full["scenario"].get(record_id) != expected:
            errors.append(f"current issue scenario mismatch at {record_id}")
    for record_id, expected in BATTLE_EXPECTED.items():
        if full["battle"].get(record_id) != expected:
            errors.append(f"battle mismatch at {record_id}")
    for record_id, expected in ISSUE_BATTLE_EXPECTED.items():
        if full["battle"].get(record_id) != expected:
            errors.append(f"current issue battle mismatch at {record_id}")

    for group in (
        ("SCE-058-0190", "SCE-059-0146", "SCE-063-0142", "SCE-065-0172"),
        ("SCE-058-0142", "SCE-059-0098", "SCE-063-0094", "SCE-065-0124"),
        ("SCE-057-0097", "SCE-058-0140", "SCE-059-0096", "SCE-063-0092", "SCE-065-0122"),
    ):
        values = {full["scenario"].get(record_id) for record_id in group}
        if len(values) != 1:
            errors.append(f"repeated scenario records disagree: {', '.join(group)}")

    remaining = read_json(ROOT / "data" / "remaining_translation_ko.json")["pools"]
    for pool, entries in POOL_EXPECTED.items():
        for japanese, expected in entries.items():
            if remaining.get(pool, {}).get(japanese) != expected:
                errors.append(f"pool mismatch at {pool}/{japanese}")

    for japanese, expected in TITLE_EXPECTED.items():
        title_values = [
            value
            for rows in read_json(ROOT / "data" / "scenario_title_ko.json")["titles"].values()
            for source, value in rows
            if source == japanese
        ]
        if title_values != [expected]:
            errors.append(f"scenario title mismatch at {japanese}")

    data_documents = [
        full,
        remaining,
        read_json(ROOT / "data" / "ssrw_glossary_ko.json"),
        read_json(ROOT / "data" / "tighten_src.json"),
        read_json(ROOT / "data" / "robotdic_ko.json"),
    ]
    superseded = (
        "류마", "브로흐", "도고라", "독고라", "브루켄그",
        "엔젤하이로", "엔젤.하이로우", "엔젤 하이로우", "천상으천하", "무.. 마",
        "외계인", "르 카인", "만젤로", "가슈란",
    )
    for document in data_documents:
        for value in all_strings(document):
            for residue in superseded:
                if residue in value:
                    errors.append(f"superseded term {residue!r} remains in Korean data")
                    break

    built_runtime_checks = 0
    if args.build_dir:
        build_dir = args.build_dir if args.build_dir.is_absolute() else ROOT / args.build_dir
        exe_path = build_dir / "SLPS_005.50"
        if not exe_path.is_file():
            # build_ssrw_full_translation keeps the rebuilt ISO members under
            # an extracted/ subdirectory before reassembly.
            exe_path = build_dir / "extracted" / "SLPS_005.50"
        if not exe_path.is_file():
            errors.append(f"built EXE not found: {exe_path}")
        else:
            sys.path.insert(0, str(ROOT / "tools"))
            import build_ssrw_full_translation as build
            from build_ssrw_screenshot_korean_test import encode_text
            from extract_ssrw_japanese_text import Codec

            codec = Codec(read_json(ROOT / "data" / "ssrw_japanese_font_mapping.json"))
            mapping_path = build_dir / "hangul_mapping.json"
            if not mapping_path.is_file():
                mapping_path = ROOT / "data" / "hangul_mapping.json"
            hangul = {
                row["character"]: int(row["glyph_index"])
                for row in read_json(mapping_path)["entries"]
            }
            image = exe_path.read_bytes()

            def check_table(table: int, index: int, korean: str, arena_end: int) -> None:
                nonlocal built_runtime_checks
                slot = table + 4 * index
                if slot + 4 > len(image):
                    errors.append(f"built pool table is truncated at {table:#x}[{index}]")
                    return
                target = slot + struct.unpack_from("<I", image, slot)[0]
                if target < slot + 4 or target >= arena_end:
                    errors.append(
                        f"built pool pointer is out of range at {table:#x}[{index}]: "
                        f"{target:#x}"
                    )
                    return
                actual = terminated(image, target, arena_end)
                if actual is None:
                    errors.append(f"built pool string is unterminated at {table:#x}[{index}]")
                    return
                expected = encode_text(korean, codec, hangul) + b"\xFF"
                built_runtime_checks += 1
                if actual != expected:
                    errors.append(
                        f"built pool mismatch at {table:#x}[{index}]: "
                        f"expected {korean!r}"
                    )

            for index, korean in MENTAL_COMMANDS.items():
                check_table(0x703B4, index, korean, 0x72434)
            for index, korean in enumerate(("육", "우", "공")):
                check_table(0x702BC, index, korean, 0x72434)
            check_table(0x702BC, 4, "공해", 0x72434)

            status = encode_text("무유", codec, hangul) + b"\xFF" * 4
            if image[0x70300:0x70300 + len(status)] != status:
                errors.append("built inline status field at 0x70300 is not 무/유")
            built_runtime_checks += 1

            fixed = read_json(ROOT / "data" / "fixed_label_translation_ko.json")["entries"]
            for identifier, entry in fixed.items():
                if entry.get("korean") not in {"지형", "정신", "정신명령"}:
                    continue
                offset = int(entry["offset"], 16)
                expected = build.encode_fixed_label_text(
                    entry["korean"], codec, hangul
                )
                built_runtime_checks += 1
                if image[offset:offset + len(expected)] != expected:
                    errors.append(f"built fixed label mismatch at {identifier}")

            for index, korean in ((55, "병사"), (100, "좀비병"), (244, "류세이")):
                slot = 0x6A938 + 4 * index
                target = slot + struct.unpack_from("<I", image, slot)[0]
                actual = terminated(image, target, 0x72434)
                expected = build.encode_pilot_name_text(
                    korean, codec, hangul
                )[0] + b"\xFF"
                built_runtime_checks += 1
                if actual != expected:
                    errors.append(
                        f"built PILOTNAME mismatch at index {index} ({korean})"
                    )

    print(f"translation counts: {counts}")
    print(
        "issue records checked: "
        f"{len(SCENARIO_EXPECTED) + len(ISSUE_SCENARIO_EXPECTED) + len(BATTLE_EXPECTED) + len(ISSUE_BATTLE_EXPECTED)}"
    )
    print(f"built executable checks: {built_runtime_checks}")
    if errors:
        print("QA FAILED:")
        for error in sorted(set(errors)):
            print(f"- {error}")
        return 1
    print("QA PASS: issue follow-ups, repeated records, terminology, and executable UI checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
