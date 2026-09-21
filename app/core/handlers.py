import logging

from fastapi import FastAPI,Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError

logger=logging.getLogger(__name__)

def _error_body(
        code:str,
        message:str,
        request_id:str|None=None,
        details:list|None=None,
)->dict:
    error:dict={"code":code,"message":message}
    if request_id:
        error["request_id"]=request_id
    if details:
        error["details"]=details
    return {"error":error}

def _rid(request:Request)->str|None:
    return getattr(request.state,"request_id",None)

def register_exception_handlers(app:FastAPI)->None:
    @app.exception_handler(AppError)
    async def handle_app_error(request:Request,exc:AppError)->JSONResponse:
        logger.warning(
            "业务异常 code=%s message=%s method=%s path=%s",
            exc.code,exc.message,request.method,request.url.path,
        )

        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code,exc.message,_rid(request)),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request:Request,exc:RequestValidationError
    )->JSONResponse:

        details=[]
        for err in exc.errors():
            loc=[str(x) for x in err.get("loc",[])]
            field=".".join(loc[1:])if len(loc)>1 else ".".join(loc)
            details.append({"field":field,"msg":err.get("msg","")})
        logger.warning("参数校验失败 path=%s details=%s",request.url.path,details)
        return JSONResponse(
            status_code=422,
            content=_error_body("validation_error","参数校验失败",_rid(request),details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request:Request,exc:StarletteHTTPException
    )->JSONResponse:
        code_map={401:"unauthorized",403:"forbidden",404:"not_found",405:"method_not_allowed"}
        code=code_map.get(exc.status_code,"http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(code,str(exc.detail),_rid(request)),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request:Request,exc:Exception)->JSONResponse:
        logger.exception("未捕获异常 method=%s path=%s",request.method,request.url.path)
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error","服务器内部错误，请稍后重试",_rid(request)),
        )

    