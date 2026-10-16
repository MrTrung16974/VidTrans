from urllib.parse import quote_plus
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

def create_social_account_router(slug, publisher, error_type, publisher_resolver=None,
                                 oauth_session_registrar=None, callback_publisher_resolver=None):
    router=APIRouter(prefix="/api/v1",tags=[f"{slug.title()} Publishing"])
    def scoped(request): return publisher_resolver(request) if publisher_resolver else publisher
    @router.get(f"/{slug}-auth")
    def status(request:Request,account_id:str=Query(default="")):
        active=scoped(request); result=active.connection_status(account_id or None); result["accounts"]=active.accounts(); return JSONResponse(result)
    @router.get(f"/{slug}-auth/connect")
    def connect(request:Request):
        try:
            url=scoped(request).authorization_url()
            if oauth_session_registrar: oauth_session_registrar(request,slug,url)
            return JSONResponse({"authorization_url":url})
        except error_type as exc: raise HTTPException(503,str(exc)) from exc
    @router.get(f"/{slug}-auth/callback")
    def callback(code:str=Query(default=""),state:str=Query(default=""),error:str=Query(default=""),error_description:str=Query(default="")):
        try:
            if error: raise error_type(error_description or error)
            active=callback_publisher_resolver(slug,state) if callback_publisher_resolver else publisher
            active.exchange_code(code,state); return RedirectResponse(url=f"/?{slug}=connected#tiktok",status_code=303)
        except (error_type, ValueError) as exc: return RedirectResponse(url=f"/?{slug}_error={quote_plus(str(exc))}#tiktok",status_code=303)
    @router.delete(f"/{slug}-auth")
    def disconnect(request:Request,account_id:str=Query(...,min_length=1,max_length=128)):
        active=scoped(request); active.disconnect(account_id); result=active.connection_status(); result["accounts"]=active.accounts(); return JSONResponse(result)
    return router
