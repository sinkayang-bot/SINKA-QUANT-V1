"""Firebase-owner command API. No OKX credentials, exchange client or trading process."""
import os
from fastapi import FastAPI,Depends,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,ConfigDict,Field
from firebase_auth import verify_owner_token
from okx_demo_control_store import FirestoreControlStore,ControlBlocked

class Command(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:str=Field(max_length=20)
    request_id:str=Field(max_length=36)


def create_app(store=None):
    app=FastAPI(title='SINKA demo control',docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(CORSMiddleware,allow_origins=['https://sinkayang-bot.github.io'],
        allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])
    def configured():
        if os.getenv('SINKA_DEMO_CONTROL')!='true': raise HTTPException(503,'Demo control disabled')
        if store is not None:return store
        from google.cloud import firestore
        return FirestoreControlStore(firestore.Client(project='sinka-quant',database='sinka-demo-control'))
    @app.middleware('http')
    async def headers(request,call_next):
        response=await call_next(request)
        response.headers['Cache-Control']='no-store, private'
        response.headers['X-Content-Type-Options']='nosniff'
        return response
    @app.get('/health')
    def health():return {'mode':'okx_demo_control','live_trading_enabled':False,'exchange_credentials_present':False}
    @app.get('/api/demo/status')
    def status(owner=Depends(verify_owner_token)):
        try:return configured().status()
        except HTTPException:raise
        except Exception:raise HTTPException(503,'Control storage unavailable') from None
    @app.post('/api/demo/commands')
    def command(body:Command,owner=Depends(verify_owner_token)):
        try:return configured().issue(body.action,body.request_id)
        except ControlBlocked as error:raise HTTPException(409,str(error)) from None
        except HTTPException:raise
        except Exception:raise HTTPException(503,'Control storage unavailable') from None
    return app

app=create_app()
