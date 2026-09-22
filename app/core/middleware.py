import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import request_id_ctx

access_logger=logging.getLogger("app.access")

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request:Request, call_next)->Response:
        request_id=request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token=request_id_ctx.set(request_id)
        request.state.request_id=request_id

        start=time.perf_counter()
        status_code=500
        try:
            response=await call_next(request)
            status_code=response.status_code
            response.headers["X-Request-ID"]=request_id
            return response
        finally:
            cost_ms=(time.perf_counter()-start)*1000
            access_logger.info(
                "http_request",
                extra={
                    "extra_fields":{
                        "method":request.method,
                        "path":request.url.path,
                        "status":status_code,
                        "cost_ms":round(cost_ms,1),
                        "client":request.client.host if request.client else"-",
                    }
                }
            )
            request_id_ctx.reset(token)
        