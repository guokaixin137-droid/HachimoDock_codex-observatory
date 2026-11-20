"""City-level Open-Meteo weather, refreshed independently of Codex telemetry."""
import json
import os
import socket
import stat
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

GROUPS = (
    ("晴",(0,)), ("晴间多云",(1,)), ("多云",(2,)), ("阴",(3,)),
    ("雾",(45,48)), ("毛毛雨",(51,53,55)), ("冻雨",(56,57,66,67)),
    ("小雨",(61,)), ("中雨",(63,)), ("大雨",(65,)), ("小雪",(71,77)),
    ("中雪",(73,)), ("大雪",(75,)), ("阵雨",(80,81,82)), ("阵雪",(85,86)),
    ("雷雨",(95,96,99)),
)
CONDITIONS = {code: label for label,codes in GROUPS for code in codes}
FALLBACK_CITY = ("北京",39.9,116.4)

def request_json(url):
    req=Request(url, headers={"User-Agent":"Codex-Observatory/3.1", "Accept":"application/json"})
    with urlopen(req, timeout=5) as response:
        data=response.read(65537)
    if len(data)>65536:
        raise ValueError("Weather response too large")
    return json.loads(data)

def coordinates(city, lat, lon):
    if (not isinstance(city,str) or not city.strip() or type(lat) not in (float,int)
        or type(lon) not in (float,int) or not (-90<=lat<=90 and -180<=lon<=180)):
        raise ValueError("Missing city coordinates")
    return (" ".join(city.split())[:28],round(lat,1),round(lon,1))

def location_setting(path):
    try:
        with path.open("rb") as f:
            config=json.loads(f.read(4097))
    except FileNotFoundError:
        return ("auto",)
    except (ValueError,OSError,UnicodeError):
        return ("invalid",)
    if isinstance(config,dict) and config.get("mode")=="manual":
        manual=config.get("manual") or {}
        try:
            return ("manual",*coordinates(manual["city"],manual["lat"],manual["lon"]))
        except (ValueError,TypeError,KeyError):
            return ("invalid",)
    return (config["mode"],) if isinstance(config,dict) and config.get("mode") in ("off","auto") else ("invalid",)

def shared_city(path, now=None):
    """Read the settings window's city over its private, read-only local socket."""
    if os.name == "nt":
        return None
    now = time.time() if now is None else now
    endpoint = path.parent / "shared-city" / "city.sock"
    try:
        directory, sock = endpoint.parent.lstat(), endpoint.lstat()
        if not (stat.S_ISDIR(directory.st_mode) and stat.S_ISSOCK(sock.st_mode)
                and directory.st_uid == sock.st_uid == os.getuid()
                and not (directory.st_mode | sock.st_mode) & 0o077):
            return None
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.45)
            client.connect(str(endpoint))
            with client.makefile("rb") as stream:
                raw = stream.readline(1025)
        if len(raw) > 1024 or not raw.endswith(b"\n"):
            return None
        value = json.loads(raw)
        if value.get("schema") != 1:
            return None
        mode, state = value.get("mode"), value.get("status")
        if mode == "off" or state == "off":
            return ("off",)
        if mode not in ("manual","auto"):
            return None
        if state == "ready" and (mode == "manual" or
            type(value.get("expiresAt")) is int and now < value["expiresAt"] <= now + 3900):
            return ("shared", *coordinates(value["city"],value["lat"],value["lon"]))
        return ("locating",) if state == "locating" else ("fallback",)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None

def get_forecast(city, fetch, now):
    name,lat,lon=city
    params=urlencode({"latitude":lat,"longitude":lon,"current":"temperature_2m,weather_code","timeformat":"unixtime"})
    data=fetch("https://api.open-meteo.com/v1/forecast?"+params)
    current=data["current"]
    temp,code,at=current["temperature_2m"],current["weather_code"],current["time"]
    if (data["current_units"]["temperature_2m"]!="°C" or type(temp) not in (int,float) or not -100<=temp<=70
        or type(at) is not int or not -300<=now-at<=3600 or type(code) is not int or code not in CONDITIONS):
        raise ValueError("Invalid or stale weather")
    display = {"singapore":"新加坡", "beijing":"北京"}.get(name.casefold(), name)
    return {"label":display+" · "+CONDITIONS[code],"temperature":temp,"at":at,"code":code}

class Weather:
    def __init__(self, config_path, fetch=request_json):
        self.config=config_path
        self.fetch=fetch
        self.lock=threading.Lock()
        self.key=None
        self.busy=False
        self.next_try=0
        self.result=None

    def snapshot(self):
        key=location_setting(self.config)
        if key == ("auto",):
            key = shared_city(self.config) or ("fallback",)
        with self.lock:
            if key!=self.key:
                self.key,self.result,self.next_try=key,None,0
            if key[0] in ("off","invalid","locating"):
                return {"label":{"off":"天气定位已关闭","invalid":"请设置天气城市","locating":"正在读取所在地"}[key[0]]}
            if not self.busy and time.monotonic()>=self.next_try:
                self.busy=True
                threading.Thread(target=self._refresh,args=(key,),daemon=True).start()
            if self.result and -300<=time.time()-self.result["at"]<=3600:
                return dict(self.result)
            return {"label":"天气更新中" if self.busy else "天气暂不可用"}

    def _refresh(self,key):
        retry=300
        fallback=False
        try:
            if key[0] in ("manual","shared"):
                city=key[1:]
            elif key[0]=="fallback":
                city, fallback = FALLBACK_CITY, True
            else:
                return
            result=get_forecast(city,self.fetch,time.time())
            result["fallback"]=fallback
            if fallback:result["label"]+="（备用）"
            with self.lock:
                if self.key==key:self.result=result
            retry=300 if fallback else 900
        except (OSError,ValueError,KeyError,TypeError):
            pass
        finally:
            with self.lock:
                self.busy=False
                if self.key==key:self.next_try=time.monotonic()+retry
