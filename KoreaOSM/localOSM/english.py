"""English presentation; native database names and identifiers stay intact."""
import json
from pathlib import Path
import re

HANGUL=re.compile('[가-힣]+')
INITIAL=('g','kk','n','d','tt','r','m','b','pp','s','ss','','j','jj','ch','k','t','p','h')
VOWEL=('a','ae','ya','yae','eo','e','yeo','ye','o','wa','wae','oe','yo','u','wo','we','wi','yu','eu','ui','i')
FINAL=('','k','k','k','n','n','n','t','l','k','m','l','l','l','p','l','m','p','p','t','t','ng','t','t','k','t','p','t')
MESSAGES={
 '기차':'Train','버스':'Bus','기차 + 버스':'Train + bus',
 '역·터미널 데이터가 설치되지 않았습니다.':'Station and terminal data is not installed.',
 '선택한 가까운 시설 사이에서 도로·선로가 모두 이어지는 경로를 찾지 못했습니다. 가까운 3곳 비교 또는 다른 접근 수단을 선택하세요.':'No connected route through the selected nearby facilities. Try the nearest 3 candidates or a different access mode.',
 '시간표를 무시하고 연결된 교통망을 이용한다고 가정한 후보 범위 최단 경로입니다.':'Shortest route within the selected candidates, assuming continuous service on the connected network.',
 '도로 구간과 기차·버스 예상 연결선을 표시했습니다. 시간표가 없어 실제 운행과 전체 도착시간은 확인되지 않았습니다.':'Road access and transit previews are shown. No timetable is available to verify service or arrival time.',
 ' 일부 시설은 도로망 연결도 확인되지 않았습니다.':' Some facilities also lack a usable road connection.',
 '선택한 수단의 역·터미널이 없습니다.':'No stations or terminals for the selected mode.',
 '가까운 역·터미널의 접근 또는 목적지 연결 도로 경로를 찾지 못했습니다. 지도에서 연결 실패 구간을 확인하세요.':'No road access or final connection for the nearby stops. Check the failed connection on the map.',
 '가장 가까운 출발·도착 시설이 같습니다. 대중교통 승차가 없는 경로를 기차·버스 이용 경로로 계산하지 않습니다.':'Both endpoints select the same stop. A journey without a transit ride is not counted as a transit route.',
 '가까운 역·터미널 사이에 연결 가능한 저장 시간표가 없습니다. 실제 미운행을 뜻하지 않습니다. 후보 3곳 비교 또는 시간표 추가를 사용하세요.':'No connection in the imported timetables between nearby stops. Try three candidates or import more journeys.',
 '선택한 가까운 역·터미널 후보와 저장 시간표 범위에서 전체 도착시간이 가장 빠른 경로입니다.':'Earliest arrival within the nearby candidates and imported timetables.',
 '연결 가능한 저장 시간표가 없습니다. 실제 미운행을 의미하지 않습니다.':'No connection in the imported timetables. This does not mean no service operates.',
 '다른 경로를 계산 중입니다. 잠시 후 다시 탐색해 주세요.':'Another route is being calculated. Please try again shortly.',
 '철도 시설망이 없어 역 사이 연결선으로 표시합니다.':'No rail graph. Showing a schematic connection.',
 '1.5 km 이내 연결 가능한 선로 후보가 없어 역 사이 연결선으로 표시합니다.':'No track candidate within 1.5 km. Showing a schematic connection.',
 '시설망에서 이어지는 선로를 찾지 못해 역 사이 연결선으로 표시합니다.':'No connected track path. Showing a schematic connection.',
 '서로 가까운 시설의 연결선입니다. 기차 운행은 확인되지 않았습니다.':'Nearby facilities share a track connection. Train service is unverified.',
 '철도 시설망을 따른 예상 연결선 · 실제 운행·선로 방향·환승 미확인':'Track-based preview; service, direction and transfers unverified',
 '차량 도로망 연결이 없어 터미널 사이 연결선으로 표시합니다.':'No connected road path. Showing a schematic terminal connection.',
 '두 시설이 같은 도로 연결점에 연결되어 시설 사이 연결선으로 표시합니다.':'Both facilities share a road node. Showing a schematic connection.',
 '승용차 도로망 기준 버스 예상선 · 실제 버스노선·전용 통행조건 미확인':'Bus preview on car roads; actual bus routes and restrictions unverified',
 '기차·버스 환승정보가 없어 시설 사이 연결선만 표시합니다.':'No train–bus transfer data. Showing a schematic connection.',
 '시간표 무시 · 연결된 선로 상시 이용 가정':'No timetable; continuous service assumed on connected tracks',
 '시간표 무시 · 도로로 연결된 터미널 간 버스 이용 가정':'No timetable; bus service assumed between road-connected terminals',
 '연결된 철도 시설망은 양방향 열차 이용 가능으로 가정':'Connected tracks are assumed usable by trains in both directions',
 '도로가 이어지는 터미널 사이는 버스 이용 가능으로 가정':'Bus service is assumed between road-connected terminals',
 '시간표·운행일·승차 대기·환승 대기 0초':'No timetable or operating calendar; boarding and transfer waits are zero',
 '열차 평균속도 설정값, 버스는 승용차 도로별 적용 속도 사용':'Trains use the configured average speed; buses use applied car-road speeds',
 '가까운 후보 범위에서 비교; 모든 역과 임의 횟수 환승의 전역 최적해는 아님':'Bounded nearby candidate comparison; not a global optimum over every stop and unlimited transfers',
}


def romanize(value):
    """Deterministic fallback transliteration, not an official-name claim."""
    def word(match):
        out=[]
        for char in match[0]:
            code=ord(char)-0xac00
            out.append(INITIAL[code//588]+VOWEL[(code%588)//28]+FINAL[code%28])
        return ''.join(out).capitalize()
    return HANGUL.sub(word,value)


class EnglishPresentation:
    def __init__(self,path=None):
        path=Path(path) if path else Path(__file__).with_name('english_names.json')
        data=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        self.by_name=data.get('by_name',{});self.by_id=data.get('by_id',{})

    def name(self,value,identifier=None):
        if not isinstance(value,str):return value
        key=str(identifier or '').removeprefix('osm:')
        if key in self.by_id:return self.by_id[key]
        english=self.by_name.get(value,value)
        return romanize(english)

    def text(self,value):
        if not isinstance(value,str) or not HANGUL.search(value):return value
        if value in MESSAGES:return MESSAGES[value]
        # Labels are generated before English presentation. Preserve their
        # direction and resolve each facility name independently.
        value=value.replace('출발지','Origin').replace('목적지','Destination').replace(' 도보 환승',' walking transfer')
        if not HANGUL.search(value):return value
        for korean,english in sorted(MESSAGES.items(),key=lambda kv:-len(kv[0])):
            if len(korean)>12:value=value.replace(korean,english)
        parts=re.split(r'( → | ↔ | · |\n)',value)
        def segment(part):
            if part.endswith(' walking transfer'):
                return self.name(part[:-len(' walking transfer')].strip())+' walking transfer'
            return self.name(part.strip()) if HANGUL.search(part) else part
        return ''.join(segment(p) for p in parts)

    def search_aliases(self,query):
        query=query.strip()[:100]
        if not query or HANGUL.search(query):return [query]
        folded=query.casefold()
        matches=[(0 if english.casefold()==folded else 1 if english.casefold().startswith(folded) else 2,
                  len(native),native) for native,english in self.by_name.items() if folded in english.casefold()]
        return [native for _,_,native in sorted(matches)[:8]]+[query]

    def payload(self,value):
        if isinstance(value,list):return [self.payload(v) for v in value]
        if not isinstance(value,dict):return value
        out={}
        for key,item in value.items():
            if key=='name' and isinstance(item,str):
                out[key]=self.name(item,value.get('id'))
            elif key in ('label','message','description','selected_label','error') and isinstance(item,str):
                out[key]=self.text(item)
            elif key in ('operator','network','source') and isinstance(item,str):
                out[key]=self.name(item)
            elif key=='assumptions' and isinstance(item,list):out[key]=[self.text(v) for v in item]
            else:out[key]=self.payload(item)
        return out
