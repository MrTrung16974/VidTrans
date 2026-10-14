from urllib.parse import quote_plus
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse

def create_social_account_router(slug, publisher, error_type):
    router=APIRouter(prefix="/api/v1",tags=[f"{slug.title()} Publishing"])
    @router.get(f"/{slug}-auth")
    def status(account_id:str=Query(default="")):
        result=publisher.connection_status(account_id or None); result["accounts"]=publisher.accounts(); return JSONResponse(result)
    @router.get(f"/{slug}-auth/connect")
    def connect():
        try: return JSONResponse({"authorization_url":publisher.authorization_url()})
        except error_type as exc: raise HTTPException(503,str(exc)) from exc
    @router.get(f"/{slug}-auth/callback")
    def callback(code:str=Query(default=""),state:str=Query(default=""),error:str=Query(default=""),error_description:str=Query(default="")):
        try:
            if error: raise error_type(error_description or error)
            publisher.exchange_code(code,state); return RedirectResponse(url=f"/?{slug}=connected#tiktok",status_code=303)
        except error_type as exc: return RedirectResponse(url=f"/?{slug}_error={quote_plus(str(exc))}#tiktok",status_code=303)
    @router.delete(f"/{slug}-auth")
    def disconnect(account_id:str=Query(...,min_length=1,max_length=128)):
        publisher.disconnect(account_id); result=publisher.connection_status(); result["accounts"]=publisher.accounts(); return JSONResponse(result)
    return router
