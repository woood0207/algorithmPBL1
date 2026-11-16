"""Offline infrastructure, validated dated timetables and earliest-arrival search.

Rail lines are display geometry only. Routing uses imported scheduled journeys;
no times, transfers, daily repetitions or operating services are inferred.
"""
from contextlib import closing
import csv
from datetime import datetime,timedelta,timezone
import heapq
import json
import math
import os
from pathlib import Path
import sqlite3

KST=timezone(timedelta(hours=9))
DEFAULT_TRANSIT=Path(__file__).resolve().parent/'transit_data'


def timestamp(value):
    if not isinstance(value,str):
        raise ValueError('Date/time must be an ISO-8601 string')
    try:
        moment=datetime.fromisoformat(value.strip().replace('Z','+00:00'))
    except ValueError as error:
        raise ValueError('Use an ISO-8601 date/time, e.g. 2026-11-15T09:00:00+09:00') from error
    if moment.tzinfo is None:
        moment=moment.replace(tzinfo=KST)
    return int(moment.timestamp())


def iso(value):
    return datetime.fromtimestamp(value,KST).isoformat()


def bounded_integer(value,label,maximum=3600):
    if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=maximum:
        raise ValueError(f'{label} must be an integer between 0 and {maximum}')
    return value


def csv_rows(path,required):
    with Path(path).open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        if not set(required)<=set(reader.fieldnames or []):
            raise ValueError('CSV requires: '+','.join(required))
        return list(reader)


class TransitStore:
    def __init__(self,directory=DEFAULT_TRANSIT):
        self.directory=Path(directory).resolve()
        path=self.directory/'manifest.json'
        self.available=path.is_file()
        self.metadata=json.loads(path.read_text(encoding='utf-8')) if self.available else {}
        if self.available and self.metadata.get('format')!='localosm-transit-v1':
            raise ValueError('Unsupported transit dataset')
        self.overview=None

    def connect(self,write=False):
        if not self.available:
            raise ValueError('Transit infrastructure has not been installed')
        path=self.directory/'transit.sqlite'
        db=sqlite3.connect(path if write else path.as_uri()+'?mode=ro',uri=not write)
        if write:
            db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA foreign_keys=ON')
        db.row_factory=sqlite3.Row
        return db

    def status(self):
        result=dict(available=self.available,api_key_configured=bool(os.environ.get('TAGO_SERVICE_KEY')),
                    scheduled_journeys=0,stops={},schedule_range=None,scope='stop-to-stop; imported data only')
        if self.available:
            with closing(self.connect()) as db:
                result['stops']={r[0]:r[1] for r in db.execute('SELECT kind,count(*) FROM stops GROUP BY kind')}
                count,first,last=db.execute('SELECT count(*),min(departure),max(departure) FROM journeys').fetchone()
                result['scheduled_journeys']=count
                result['schedule_range']=[iso(first),iso(last)] if count else None
                result['transfers']=db.execute('SELECT count(*) FROM transfers').fetchone()[0]
        return result

    @staticmethod
    def stop_dict(row):
        return dict(id=row['id'],name=row['name'],kind=row['kind'],coordinate=[row['lat'],row['lon']],
                    source=row['source'],details=json.loads(row['details']))

    def stops(self,query='',kind='all',bounds=None,limit=30):
        if kind not in {'all','rail_station','bus_terminal','interchange'}:
            raise ValueError('Invalid transit stop kind')
        where,values=[],[]
        if query.strip():
            text=query.strip()[:100]
            literal=text.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            if text.endswith('역') and len(text)>1:
                alias=text[:-1].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
                where.append("(s.name LIKE ? ESCAPE '\\' OR (s.kind='rail_station' AND s.name LIKE ? ESCAPE '\\'))")
                values.extend(('%'+literal+'%','%'+alias+'%'))
            else:
                where.append("s.name LIKE ? ESCAPE '\\'");values.append('%'+literal+'%')
        if kind!='all':
            where.append('s.kind=?');values.append(kind)
        join=''
        if bounds is not None:
            west,south,east,north=bounds
            if not (-180<=west<east<=180 and -90<=south<north<=90):
                raise ValueError('Invalid map bounds')
            join=' JOIN stop_index r ON r.rowid=s.rowid'
            where+=['r.min_lon<=?','r.max_lon>=?','r.min_lat<=?','r.max_lat>=?']
            values += [east,west,north,south]
        with closing(self.connect()) as db:
            rows=db.execute('SELECT s.* FROM stops s'+join+(' WHERE '+' AND '.join(where) if where else '')+
                            ' ORDER BY s.name,s.id LIMIT ?',values+[limit+1]).fetchall()
        return dict(stops=[self.stop_dict(r) for r in rows[:limit]],truncated=len(rows)>limit)

    def map_data(self,bounds,zoom):
        if not math.isfinite(zoom) or not 0<=zoom<=22:
            raise ValueError('Invalid zoom')
        result=self.stops(bounds=bounds,limit=1500)
        if zoom<10:
            if self.overview is None:
                self.overview=json.loads((self.directory/'rail_overview.json').read_text(encoding='utf-8'))
            result['railways']=self.overview
        else:
            west,south,east,north=bounds
            with closing(self.connect()) as db:
                rows=db.execute('''SELECT f.geojson FROM rail_index r JOIN rails f ON f.id=r.id
                  WHERE r.min_lon<=? AND r.max_lon>=? AND r.min_lat<=? AND r.max_lat>=? LIMIT 4001''',
                  (east,west,north,south)).fetchall()
            result['railways']=dict(type='FeatureCollection',features=[json.loads(r[0]) for r in rows[:4000]])
            result['truncated']=result['truncated'] or len(rows)>4000
        result['infrastructure_only']=True
        return result

    def import_stops(self,rows):
        clean=[]
        for row in rows:
            stop_id,name,kind,source=(str(row.get(k,'')).strip() for k in ('id','name','kind','source'))
            if not stop_id or len(stop_id)>160 or not name or not source or kind not in {'rail_station','bus_terminal','interchange'}:
                raise ValueError('Stop id, name, kind and source are required')
            lat,lon=float(row['latitude']),float(row['longitude'])
            if not (math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180):
                raise ValueError('Invalid stop coordinate')
            clean.append((stop_id,name,kind,lat,lon,source,'{}'))
        if len({r[0] for r in clean})!=len(clean):
            raise ValueError('Duplicate stop IDs in import')
        with closing(self.connect(write=True)) as db,db:
            for row in clean:
                prior=db.execute('SELECT kind FROM stops WHERE id=?',(row[0],)).fetchone()
                if prior and prior[0]!=row[2]:
                    raise ValueError('Cannot change the kind of an existing stop')
                db.execute('''INSERT INTO stops VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,lat=excluded.lat,lon=excluded.lon,source=excluded.source''',row)
                rid=db.execute('SELECT rowid FROM stops WHERE id=?',(row[0],)).fetchone()[0]
                db.execute('DELETE FROM stop_index WHERE rowid=?',(rid,))
                db.execute('INSERT INTO stop_index VALUES(?,?,?,?,?)',(rid,row[4],row[4],row[3],row[3]))
        return len(clean)

    def import_journeys(self,rows):
        clean=[]
        with closing(self.connect()) as db:
            kinds={r[0]:r[1] for r in db.execute('SELECT id,kind FROM stops')}
        for row in rows:
            values={k:str(row.get(k,'')).strip() for k in ('id','trip_id','mode','from_stop','to_stop','source')}
            if any(not values[k] for k in ('id','mode','from_stop','to_stop','source')) or values['mode'] not in {'rail','bus'}:
                raise ValueError('Journey id, mode (rail/bus), from_stop, to_stop and source are required')
            if values['from_stop']==values['to_stop'] or any(kinds.get(values[k]) not in
                    ({'rail_station','interchange'} if values['mode']=='rail' else {'bus_terminal','interchange'}) for k in ('from_stop','to_stop')):
                raise ValueError('Journey stops are missing or incompatible with the mode')
            departure,arrival=timestamp(row['departure']),timestamp(row['arrival'])
            if arrival<departure:
                raise ValueError('Arrival must not precede departure. Include the next date for overnight services.')
            fare_text=str(row.get('fare_krw','')).strip()
            fare=int(fare_text) if fare_text else None
            if fare is not None and fare<0:
                raise ValueError('Fare must be non-negative or empty (unknown)')
            clean.append((values['id'],values['trip_id'] or values['id'],values['mode'],values['from_stop'],values['to_stop'],
                          departure,arrival,fare,values['source'],datetime.now(timezone.utc).isoformat()))
        if len({r[0] for r in clean})!=len(clean):
            raise ValueError('Duplicate journey IDs in import')
        with closing(self.connect(write=True)) as db,db:
            db.executemany('''INSERT INTO journeys VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
              trip_id=excluded.trip_id,mode=excluded.mode,from_stop=excluded.from_stop,to_stop=excluded.to_stop,
              departure=excluded.departure,arrival=excluded.arrival,fare_krw=excluded.fare_krw,
              source=excluded.source,imported_utc=excluded.imported_utc''',clean)
        return len(clean)

    def import_transfers(self,rows):
        clean=[]
        for row in rows:
            duration=int(row['duration_s'])
            if not 1<=duration<=86400 or row['from_stop']==row['to_stop'] or not str(row.get('source','')).strip():
                raise ValueError('Transfers require distinct stops, positive duration and a source')
            clean.append((row['from_stop'],row['to_stop'],duration,row['source']))
        with closing(self.connect(write=True)) as db,db:
            db.executemany('INSERT INTO transfers VALUES(?,?,?,?) ON CONFLICT(from_stop,to_stop) DO UPDATE SET duration_s=excluded.duration_s,source=excluded.source',clean)
        return len(clean)

    def plan(self,start,end,departure,min_transfer_s=600,boarding_s=300,modes='all'):
        start_time=timestamp(departure)
        min_transfer_s=bounded_integer(min_transfer_s,'min_transfer_s')
        boarding_s=bounded_integer(boarding_s,'boarding_s')
        if modes not in {'all','rail','bus'}:
            raise ValueError('Transit modes must be all, rail or bus')
        if not isinstance(start,str) or not isinstance(end,str):
            raise ValueError('Select departure and arrival stop IDs')
        with closing(self.connect()) as db:
            raw=db.execute('SELECT * FROM stops WHERE id IN (?,?)',(start,end)).fetchall()
            stops={r['id']:self.stop_dict(r) for r in raw}
            if start not in stops or end not in stops:
                raise ValueError('Unknown departure or arrival stop')
            result=dict(reachable=False,departure=iso(start_time),arrival=None,duration_s=None,legs=[],
                        start=stops[start],end=stops[end],scope='selected stops and imported timetables only',geometry='schematic',
                        min_transfer_s=min_transfer_s,boarding_s=boarding_s,coverage=self.status())
            if start==end:
                return dict(result,reachable=True,arrival=iso(start_time),duration_s=0,waiting_s=0,transfers=0,fare_krw=0)
            origin=(start,None)
            costs,parents={origin:start_time},{}
            queue=[(start_time,0,origin)]
            serial=0
            while queue:
                now,_,state=heapq.heappop(queue)
                if now!=costs[state]:
                    continue
                stop,last_trip=state
                if stop==end:
                    legs=[];cursor=state
                    while cursor!=origin:
                        cursor,leg=parents[cursor]
                        legs.append(leg)
                    legs.reverse()
                    fares=[x['fare_krw'] for x in legs if x['mode']!='walk']
                    # Summing through-trip quotes could double count a single fare.
                    trip_ids=[(x['mode'],x['trip_id']) for x in legs if x['mode']!='walk']
                    fare=sum(fares) if all(f is not None for f in fares) and len(set(trip_ids))==len(trip_ids) else None
                    boardings,last_boarded,walked=0,None,False
                    for leg in legs:
                        if leg['mode']=='walk':
                            walked=True
                        else:
                            boarding=(leg['mode'],leg['trip_id'])
                            if last_boarded is None or last_boarded!=boarding or walked:
                                boardings+=1
                            last_boarded,walked=boarding,False
                    return dict(result,reachable=True,arrival=iso(now),duration_s=now-start_time,legs=legs,
                                waiting_s=sum(x.get('waiting_s',0) for x in legs),
                                transfers=max(0,boardings-1),fare_krw=fare)
                threshold=now+(boarding_s if last_trip is None else 0)
                rows=db.execute('SELECT * FROM journeys WHERE from_stop=? AND departure>=? ORDER BY departure',(stop,threshold))
                candidates=[]
                for row in rows:
                    if modes!='all' and row['mode']!=modes:
                        continue
                    vehicle=(row['mode'],row['trip_id'])
                    margin=boarding_s if last_trip is None else 0 if last_trip==vehicle else min_transfer_s
                    if row['departure']<now+margin:
                        continue
                    candidates.append(((row['to_stop'],vehicle),row['arrival'],dict(row)))
                for row in db.execute('SELECT * FROM transfers WHERE from_stop=?',(stop,)):
                    candidates.append(((row['to_stop'],('__walk__',row['to_stop'])),now+row['duration_s'],
                        dict(id=None,trip_id=None,mode='walk',from_stop=stop,to_stop=row['to_stop'],departure=now,
                             arrival=now+row['duration_s'],fare_krw=0,source=row['source'])))
                for next_state,arrival,leg in candidates:
                    if arrival>=costs.get(next_state,math.inf):
                        continue
                    for key in ('from_stop','to_stop'):
                        sid=leg[key]
                        if sid not in stops:
                            stops[sid]=self.stop_dict(db.execute('SELECT * FROM stops WHERE id=?',(sid,)).fetchone())
                    leg=dict(mode=leg['mode'],trip_id=leg['trip_id'],departure=iso(leg['departure']),arrival=iso(leg['arrival']),
                             waiting_s=leg['departure']-now,duration_s=leg['arrival']-leg['departure'],
                             from_stop=stops[leg['from_stop']],to_stop=stops[leg['to_stop']],fare_krw=leg['fare_krw'],
                             source=leg['source'],geometry='schematic')
                    costs[next_state]=arrival;parents[next_state]=(state,leg)
                    serial+=1;heapq.heappush(queue,(arrival,serial,next_state))
        return dict(result,error='no_cached_itinerary',message='연결 가능한 저장 시간표가 없습니다. 실제 미운행을 의미하지 않습니다.')


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=DEFAULT_TRANSIT)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('status')
    p=sub.add_parser('search');p.add_argument('query')
    for command in ('import-stops','import-journeys','import-transfers'):
        p=sub.add_parser(command);p.add_argument('csv',type=Path)
    args=parser.parse_args();store=TransitStore(args.data_dir)
    if args.command=='status':
        result=store.status()
    elif args.command=='search':
        result=store.stops(args.query)
    else:
        fields={'import-stops':('id','name','kind','latitude','longitude','source'),
                'import-journeys':('id','mode','from_stop','to_stop','departure','arrival','source'),
                'import-transfers':('from_stop','to_stop','duration_s','source')}
        result=dict(imported=getattr(store,args.command.replace('-','_'))(csv_rows(args.csv,fields[args.command])))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
