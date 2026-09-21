class AppError(Exception):
    status_code:int=400
    code:str="app_error"
    message:str="请求处理失败"

    def __init__(
        self,
        message:str|None=None,
        *,
        code:str|None=None,
        status_code:int|None=None
        )->None:
        if message:
            self.message=message
        if code:
            self.code=code
        if status_code:
            self.status_code=status_code
        super().__init__(self.message)

class BadRequestError(AppError):
    status_code=400
    code="bad_request"
    message="请求参数不合法"

class AuthError(AppError):
    status_code=401
    code="unauthorized"
    message="身份认证失败"

class ForbiddenError(AppError):
    status_code=403
    code="forbidden"
    message="没有权限执行该操作"

class NotFoundError(AppError):
    status_code=404
    code="not_found"
    message="资源不存在"

class ConflictError(AppError):
    status_code=409
    code="Conflict"
    message="资源冲突"

class RateLimitError(AppError):
    status_code=429
    code="rate_limited"
    message="请求过于频繁，请稍后再试"

class UpstreamError(AppError):
    status_code=502
    code="upstream_error"
    message="上游服务暂不可用"

class LLMTimeoutError(UpstreamError):
    code="llm_timeout"
    message="模型响应超时，请稍后重试"

