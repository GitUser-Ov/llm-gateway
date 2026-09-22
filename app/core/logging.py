import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime

request_id_ctx:ContextVar[str]=ContextVar("request_id",default='-')

class RequestIdFilter(logging.Filter):
    def filter(self,record:logging.LogRecord)->bool:
        record.request_id=request_id_ctx.get()
        return True

def _now()->str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")

class JsonFormatter(logging.Formatter):
    def format(self,record:logging.LogRecord)->str:
        payload:dict={
        "ts":_now(),
        "level":record.levelname,
        "logger":record.name,
        "request_id":getattr(record,"request_id","-"),
        "message":record.getMessage()
        }

        extra=getattr(record,"extra_fields",None)
        if isinstance(extra,dict):
            payload.update(extra)

        if record.exc_info:
            payload["exc"]=self.formatException(record.exc_info)
        return json.dumps(payload,ensure_ascii=False)

class HumanFormatter(logging.Formatter):
    def format(self,record:logging.LogRecord)->str:
        rid=getattr(record,"request_id","-")
        base=(
        f"{_now()}|{record.levelname:<7}|{rid}|"
        f"{record.name}|{record.getMessage()}"
        )
        extra=getattr(record,"extra_fields",None)
        if isinstance(extra,dict)and extra:
            base+="|"+" ".join(f"{k}={v}"for k,v in extra.items())

        if record.exc_info:
            base+="\n"+self.formatException(record.exc_info)

        return base

def setup_logging(level:str="INFO",json_output:bool=False)->None:
    formatter=JsonFormatter() if json_output else HumanFormatter()
    handler=logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    root=logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in ("uvicorn","uvicorn.error","uvicorn.access"):
        lg=logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate=True

    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("httpcore").setLevel("WARNING")

    