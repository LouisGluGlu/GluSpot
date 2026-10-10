"use strict";
// GluSpot original adapter. Only stdio carries account envelopes; no API server.
const path = require("node:path");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const readline = require("node:readline");
const writeProtocol = process.stdout.write.bind(process.stdout);
for (const name of ["log", "warn", "error", "info", "debug", "trace"]) console[name] = () => {};
process.env.NODE_ENV = "test";
process.env.QQ_AUTH_STATE_PATH = "memory";
process.env.ENABLE_RANDOM_CN_IP = "false";
delete process.env.NETEASE_COOKIE;
delete process.env.QQ_AUTH_SESSION_PATH;
const moduleRoot = process.env.GLUSPOT_ONLINE_MUSIC_MODULE_ROOT;
if (!moduleRoot) process.exit(2);
const sources = new Map();
const loginAttempts = new Map();
function fail(code) { const error = new Error(code); error.safeCode = code; throw error; }
function requireId(value) { const id = String(value || ""); if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) fail("invalid_response"); return id; }
function mergeCookies(target, incoming) {
  const lines = Array.isArray(incoming) ? incoming : String(incoming || "").split(/;\s*/);
  for (const line of lines) {
    const part = String(line).split(";")[0], split = part.indexOf("=");
    if (split < 1) continue;
    const key = part.slice(0, split).trim(), val = part.slice(split + 1).trim();
    if (/^(path|domain|expires|max-age|secure|httponly|samesite)$/i.test(key)) continue;
    if (/^[A-Za-z0-9_]+$/.test(key)) target[key] = val;
  }
}
function safeAccount(source, profile) {
  if (!profile || typeof profile !== "object") return {source_id: source, logged_in: false};
  const id = profile.userId || profile.userid || profile.user_id || profile.str_musicid || profile.musicid || profile.uin;
  if (!id || String(id) === "0") return {source_id: source, logged_in: false};
  return {source_id: source, logged_in: true, user_id: String(id), nickname: String(profile.nickname || profile.nickName || profile.nick || profile.name || ""), avatar_url: String(profile.avatarUrl || profile.avatarurl || profile.pic || profile.headurl || profile.avatar || "")};
}
function ncmState(credential, repository=sources) {
  let state = repository.get("netease");
  if (!state) {
    const packageRoot = path.join(moduleRoot, "@neteasecloudmusicapienhanced", "api");
    // The API request module expects an anonymous-token file initialized by
    // its main entry. Keep this empty owned file inside the isolated cache.
    const anonymousToken = path.join(os.tmpdir(), "anonymous_token");
    if (!fs.existsSync(anonymousToken)) fs.writeFileSync(anonymousToken, "", {flag:"wx",mode:0o600});
    state = {root:packageRoot, request:require(path.join(packageRoot,"util","request.js")), cookie:{}};
    repository.set("netease",state);
  }
  if (credential && credential.cookie && typeof credential.cookie === "object") state.cookie = {...credential.cookie};
  return state;
}
async function ncmCall(state, method, params = {}) {
  const query = {...params, cookie:{...state.cookie}, timeout:12000, randomCNIP:false, ip:"", realIP:"", timestamp:Date.now()};
  let result;
  // The pinned package's QR-check catch references an out-of-scope result.
  // Use its verified request API directly so network errors remain errors.
  if (method === "login_qr_check") {
    try {
      result = await state.request("/api/login/qrcode/client/login",{key:query.key,type:3},{crypto:"",cookie:query.cookie,timeout:12000,randomCNIP:false});
    } catch (error) {
      // Pending/expired QR statuses may be carried by a rejected result. Accept
      // only the verified protocol codes, never transform transport failure.
      if (error && error.body && [800,801,802,803].includes(Number(error.body.code))) result=error;
      else throw error;
    }
  } else if (method === "song_url_v1") {
    // No unblock/matching operation is reachable through this bridge.
    result = await state.request("/api/song/enhance/player/url/v1",{ids:JSON.stringify([Number(query.id)]),level:query.level || "standard",encodeType:"mp3"},{crypto:"xeapi",cookie:query.cookie,timeout:12000,randomCNIP:false});
  } else {
    const allowed = new Set(["login_qr_key","login_qr_create","login_status","user_playlist","playlist_track_all","recommend_songs","personal_fm","lyric_new","lyric","cloudsearch"]);
    if (!allowed.has(method)) fail("unsupported");
    const fn = require(path.join(state.root,"module",method + ".js"));
    result = await fn(query,state.request);
  }
  if (!result || typeof result.body !== "object") fail("invalid_response");
  mergeCookies(state.cookie,result.cookie);
  return result.body;
}
function qqState(credential, repository=sources) {
  let state = repository.get("qq");
  if (!state) {
    const root = path.join(moduleRoot,"@yakult-green-tea","qq-music-api");
    // These exact exports/contracts are verified against pinned 3.1.4.
    const mod = require(path.join(root,"dist","src","services","auth","qrLogin.node.js"));
    state = {service:mod.createNodeQrLoginService(), sessions:[], token:""};
    state.service.configureAuthSessionRepository({kind:"gluspot-protected-host",load:() => state.sessions,save:value => {state.sessions = JSON.parse(JSON.stringify(value));}});
    repository.set("qq",state);
  }
  if (credential && Array.isArray(credential.sessions)) {
    state.sessions = credential.sessions;
    state.token = String(credential.token || "");
    state.service.configureAuthSessionRepository({kind:"gluspot-protected-host",load:() => state.sessions,save:value => {state.sessions = JSON.parse(JSON.stringify(value));}});
  }
  return state;
}
function kugouState(credential, repository=sources) {
  let state=repository.get("kugou");
  if (!state) {
    const root=path.join(moduleRoot,"kugoumusicapi");
    // Import only requested modules, bypassing the upstream HTTP/server/.env entry.
    const util=require(path.join(root,"util","index.js"));
    const guid=crypto.randomBytes(16).toString("hex");
    state={root, request:require(path.join(root,"util","request.js")).createRequest, cookie:{KUGOU_API_GUID:guid,KUGOU_API_MID:util.calculateMid(guid),KUGOU_API_WEBGL:crypto.randomBytes(8).readBigUInt64BE().toString(),KUGOU_API_DEV:crypto.randomBytes(5).toString("hex").toUpperCase(),KUGOU_API_MAC:"02:00:00:00:00:00",KUGOU_API_PLATFORM:"normal"}};
    repository.set("kugou",state);
  }
  if (credential && credential.cookie && typeof credential.cookie==="object") state.cookie={...state.cookie,...credential.cookie};
  return state;
}
async function kgCall(state,method,params={}) {
  const allowed=new Set(["register_dev","login_qr_key","login_qr_create","login_qr_check","user_detail","user_playlist","playlist_track_all","song_url","lyric","search_lyric","search"]);
  if(!allowed.has(method)) fail("unsupported");
  const fn=require(path.join(state.root,"module",method+".js"));
  const result=await fn({...params,cookie:{...state.cookie}},state.request);
  if(!result || typeof result.body!=="object") fail("invalid_response");
  mergeCookies(state.cookie,result.cookie);
  return result.body;
}
async function operationState(source,op,p,credential,createState) {
  if(op==="begin_login") {
    // Ordinary catalog requests may carry an older account while a QR waits.
    // Give each attempt its own state, including a separate QQ session resolver.
    for(const [key,attempt] of loginAttempts) {
      if(attempt.source===source || attempt.expiresAt<=Date.now()) {
        loginAttempts.delete(key);
        if(attempt.source==="qq") await attempt.state.service.logout("");
      }
    }
    return createState(null,new Map());
  }
  if(op==="poll_login" || op==="cancel_login") {
    const attempt=loginAttempts.get(source+":"+requireId(p.key));
    if(!attempt || attempt.expiresAt<=Date.now()) fail("expired");
    return attempt.state;
  }
  return createState(credential);
}
function rememberLogin(source,key,state) {
  loginAttempts.set(source+":"+requireId(key),{source,state,expiresAt:Date.now()+180000});
}
function validSecret(value) {return typeof value==="string" && value.length>0 && value!=="undefined" && value!=="null";}
function searchInput(p) {
  if(typeof p.query!=="string" || !p.query.trim() || Array.from(p.query).length>200 || /[\x00-\x1f]/.test(p.query) || !["songs","playlists"].includes(p.kind)) fail("invalid_response");
  if(!Number.isSafeInteger(p.limit) || p.limit<1 || p.limit>100 || !Number.isSafeInteger(p.offset) || p.offset<0 || p.offset>100000) fail("invalid_response");
  return {query:p.query.trim(),kind:p.kind,limit:p.limit,offset:p.offset,page:Math.floor(p.offset/p.limit)+1};
}
function searchWindow(rows,total,p,nextPage) {
  if(!Array.isArray(rows)) fail("invalid_response");
  const start=p.page===undefined?0:p.offset%p.limit;
  const items=rows.slice(start,start+p.limit).filter(item=>item && typeof item==="object" && !Array.isArray(item));
  const count=total!==undefined && total!==null && total!=="" && Number.isSafeInteger(Number(total)) && Number(total)>=0?Number(total):null;
  const boundary=p.page===undefined?p.offset+Math.min(rows.length,p.limit):p.page*p.limit;
  const more=nextPage!==undefined?Number(nextPage)>0:count!==null?boundary<count:rows.length>=p.limit;
  return {items,total:count,has_more:more,next_offset:more?(nextPage!==undefined?(Number(nextPage)-1)*p.limit:boundary):p.offset+Math.min(Math.max(0,rows.length-start),p.limit)};
}
async function qqSearch(state,p) {
  // Pinned legacy search hardcodes song/t=0. Use its verified HTTP client for
  // the separately verified public musicu song and playlist search contract.
  if(!state.searchHttp) state.searchHttp=require(path.join(moduleRoot,"@yakult-green-tea","qq-music-api","dist","src","services","auth","httpClient.js")).default();
  const response=await state.searchHttp.post("https://u.y.qq.com/cgi-bin/musicu.fcg",{
    comm:{ct:11,cv:"1003006",v:"1003006",tmeAppID:"qqmusiclight",uid:"0",uin:"0"},
    request:{module:"music.search.SearchCgiService",method:"DoSearchForQQMusicDesktop",param:{query:p.query,search_type:p.kind==="songs"?0:3,num_per_page:p.limit,page_num:p.page,highlight:0,nqc_flag:0,page_id:1,grp:1,remoteplace:"search.android.keyboard",search_id:crypto.randomInt(1000000000000).toString()}}
  },{timeout:12000,headers:{Cookie:"tmeLoginType=-1;",Referer:"https://y.qq.com/","User-Agent":"okhttp/3.14.9"}});
  const body=response.data,request=body && body.request,data=request && request.data;
  if(!body || Number(body.code)!==0 || !request || Number(request.code)!==0 || !data || !data.body || !data.meta || Number(data.meta.is_filter || 0)!==0) fail("invalid_response");
  const section=data.body[p.kind==="songs"?"song":"songlist"];
  return searchWindow(section && section.list,data.meta.sum,p,data.meta.nextpage);
}
async function dispatch(source,op,p,credential,context) {
  if(source==="netease") {
    const state=await operationState(source,op,p,credential,ncmState);context.state=state;
    if(op==="begin_login") {
      const response=await ncmCall(state,"login_qr_key");
      const key=response.data && (response.data.unikey || response.data.data && response.data.data.unikey);
      if(!key) fail("invalid_response");
      const qr=await ncmCall(state,"login_qr_create",{key,qrimg:true,platform:"pc"});
      rememberLogin(source,String(key),state);
      return {key:String(key),qr_image:qr.data && qr.data.qrimg,qr_url:qr.data && qr.data.qrurl,ttl:180};
    }
    if(op==="poll_login") {
      const body=await ncmCall(state,"login_qr_check",{key:requireId(p.key)});
      const code=Number(body.code);
      if(code===803 && !validSecret(state.cookie.MUSIC_U)) fail("invalid_response");
      return {state:({800:"expired",801:"waiting",802:"scanned",803:"confirmed"})[code] || "error"};
    }
    if(op==="cancel_login") return {state:"cancelled"};
    if(op==="account_status") {
      const body=await ncmCall(state,"login_status");
      return safeAccount(source,body.data && body.data.profile || body.profile);
    }
    if(op==="logout") { state.cookie={}; return {}; }
    if(!state.cookie.MUSIC_U) fail("auth_required");
    if(op==="search") {
      const params=searchInput(p),body=await ncmCall(state,"cloudsearch",{keywords:params.query,type:params.kind==="songs"?1:1000,limit:params.limit,offset:params.offset});
      if(Number(body.code)!==200 || !body.result || typeof body.result!=="object") fail("invalid_response");
      delete params.page;
      return searchWindow(body.result[params.kind] || [],body.result[params.kind==="songs"?"songCount":"playlistCount"],params);
    }
    if(op==="playlists") return await ncmCall(state,"user_playlist",{uid:requireId(p.user_id),limit:p.limit,offset:p.offset});
    if(op==="playlist_tracks") return await ncmCall(state,"playlist_track_all",{id:requireId(p.playlist_id),limit:p.limit,offset:p.offset});
    if(op==="daily_recommendations") return await ncmCall(state,"recommend_songs");
    if(op==="personal_fm") return await ncmCall(state,"personal_fm");
    if(op==="stream_url") return await ncmCall(state,"song_url_v1",{id:requireId(p.song_id),level:"standard"});
    if(op==="lyrics") {
      let newer={};
      try {newer=await ncmCall(state,"lyric_new",{id:requireId(p.song_id)});} catch(_) {}
      if(!newer.lrc || !newer.tlyric) {
        const older=await ncmCall(state,"lyric",{id:requireId(p.song_id)});
        for(const key of ["lrc","yrc","tlyric","ytlrc","romalrc","yromalrc"]) if(!newer[key] && older[key]) newer[key]=older[key];
      }
      return newer;
    }
  } else if(source==="qq") {
    const state=await operationState(source,op,p,credential,qqState);context.state=state;
    if(op==="begin_login") {const key=await state.service.createSession("qq");const qr=await state.service.createQr(key);rememberLogin(source,key,state);return {key,qr_image:qr,ttl:180};}
    if(op==="poll_login") {
      const body=await state.service.checkQr(requireId(p.key),1200);
      if(body.code===803) {
        if(typeof body.cookie!=="string" || !body.cookie.startsWith("qqmusic_session=")) fail("invalid_response");
        const token=body.cookie.slice("qqmusic_session=".length).split(";")[0];
        if(!validSecret(token) || !Array.isArray(state.sessions) || !state.sessions.some(item=>item.token===token)) fail("invalid_response");
        state.token=token;
      }
      return {state:({800:"expired",801:"waiting",802:"scanned",803:"confirmed"})[body.code] || "error"};
    }
    if(op==="cancel_login") {state.service.cancelSession(requireId(p.key));return {state:"cancelled"};}
    if(op==="account_status") return safeAccount(source,await state.service.getLoginStatus(state.token));
    if(op==="logout") {await state.service.logout(state.token);state.token="";state.sessions=[];return {};}
    if(!state.token) fail("auth_required");
    if(op==="search") return await qqSearch(state,searchInput(p));
    if(op==="playlists") return await state.service.getUserPlaylists(state.token);
    if(op==="playlist_tracks") {
      try {
        const body=await state.service.getOwnedPlaylistSongs(state.token,{disstid:requireId(p.playlist_id),offset:p.offset,limit:Math.min(100,p.limit)});
        return {...body,_windowed:true};
      } catch (_) {
        if(p.offset>=1000) fail("unsupported");
        return await state.service.getAuthenticatedSongListDetail(state.token,requireId(p.playlist_id),{limit:Math.min(1000,p.offset+p.limit)});
      }
    }
    if(op==="stream_url") {
      const id=requireId(p.song_id),body=await state.service.getMusicPlay(state.token,id,"128");
      return body && body[id] || {url:"",restriction:"unavailable"};
    }
  } else if(source==="kugou") {
    const state=await operationState(source,op,p,credential,kugouState);context.state=state;
    if(op==="begin_login") {
      if(!state.cookie.dfid) await kgCall(state,"register_dev");
      const body=await kgCall(state,"login_qr_key");
      const key=body.data && (body.data.qrcode || body.data.key || body.data.unikey);
      if(!key) fail("invalid_response");
      const qr=await kgCall(state,"login_qr_create",{key,qrimg:true});
      rememberLogin(source,String(key),state);
      return {key:String(key),qr_image:qr.data && qr.data.base64,qr_url:qr.data && qr.data.url,ttl:180};
    }
    if(op==="poll_login") {
      const body=await kgCall(state,"login_qr_check",{key:requireId(p.key)}),data=body.data || {};
      if(Number(data.status)===4) {
        if(!validSecret(String(data.token || "")) || !/^[1-9][0-9]*$/.test(String(data.userid || ""))) fail("invalid_response");
        state.cookie.token=String(data.token);state.cookie.userid=String(data.userid);
      }
      return {state:({0:"expired",1:"waiting",2:"scanned",3:"scanned",4:"confirmed"})[Number(data.status)] || "error"};
    }
    if(op==="cancel_login") return {state:"cancelled"};
    if(op==="account_status") {
      if(!state.cookie.token || !state.cookie.userid) return {source_id:source,logged_in:false};
      const body=await kgCall(state,"user_detail");
      if(Number(body.status)!==1 || body.error_code!==undefined && Number(body.error_code)!==0 || !body.data || typeof body.data!=="object" || Array.isArray(body.data) || Object.keys(body.data).length===0) return {source_id:source,logged_in:false};
      return safeAccount(source,{...(body.data || {}),userid:state.cookie.userid});
    }
    if(op==="logout") {delete state.cookie.token;delete state.cookie.userid;return {};}
    if(!state.cookie.token || !state.cookie.userid) fail("auth_required");
    if(op==="search") {
      const params=searchInput(p),body=await kgCall(state,"search",{keywords:params.query,type:params.kind==="songs"?"song":"special",page:params.page,pagesize:params.limit});
      const data=body.data;
      // Pinned request.js can pass string status/error codes through unchanged.
      // Match account verification; a platform denial is never an empty page.
      if(Number(body.status)!==1 || body.error_code!==undefined && Number(body.error_code)!==0 || !data || typeof data!=="object" || Array.isArray(data)) fail("invalid_response");
      return searchWindow(data.lists || data.info || [],data.total,params);
    }
    if(op==="playlists") return await kgCall(state,"user_playlist",{userid:state.cookie.userid,pagesize:p.limit,page:Math.floor(p.offset/p.limit)+1});
    if(op==="playlist_tracks") return await kgCall(state,"playlist_track_all",{id:requireId(p.playlist_id),pagesize:p.limit,page:Math.floor(p.offset/p.limit)+1});
    if(op==="stream_url") return await kgCall(state,"song_url",{hash:requireId(p.song_id),quality:"128"});
  }
  fail("unsupported");
}
function envelope(source,state=sources.get(source)) {
  if(!state) return null;
  if(source==="qq") return {token:state.token,sessions:state.sessions};
  return {cookie:state.cookie};
}
let sequence=Promise.resolve();
const input=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
input.on("line",line => {
  if(Buffer.byteLength(line)>8*1024*1024) process.exit(3);
  sequence=sequence.then(async() => {
    let request;
    try {
      request=JSON.parse(line);
      if(!Number.isSafeInteger(request.id) || !["netease","qq","kugou"].includes(request.source) || !request.params || typeof request.params!=="object") fail("invalid_response");
      let timer;
      const timeout=new Promise((_,reject) => {timer=setTimeout(() => {const e=new Error("timeout");e.safeCode="timeout";reject(e);},15000);});
      let data;const context={};
      try {data=await Promise.race([dispatch(request.source,request.operation,request.params,request.credential,context),timeout]);} finally {clearTimeout(timer);}
      const credential=envelope(request.source,context.state);
      if(request.operation==="cancel_login" || request.operation==="poll_login" && ["confirmed","expired","error","cancelled"].includes(data.state)) {
        loginAttempts.delete(request.source+":"+request.params.key);
        if(request.source==="qq") await context.state.service.logout("");
      }
      writeProtocol(JSON.stringify({id:request.id,ok:true,data,credential})+"\n");
    } catch(error) {
      const safeStage = request && ["begin_login","poll_login","account_status"].includes(request.operation) ? request.operation : "catalog";
      const safeFailure = error.code && ["ENOTFOUND","ECONNRESET","ECONNREFUSED","ETIMEDOUT","ENOENT"].includes(error.code) ? error.code : (error.response && Number.isInteger(error.response.status) ? "HTTP_"+error.response.status : (error.safeCode || "UPSTREAM"));
      writeProtocol(JSON.stringify({id:request && request.id,ok:false,error:error.safeCode || "network",failure:safeFailure,stage:safeStage})+"\n");
      // A timed-out upstream can still mutate authentication state later. Exit
      // instead of allowing that abandoned generation to service another request.
      if(error.safeCode==="timeout") process.exit(4);
    }
  });
});
input.on("close",() => {sequence.finally(() => process.exit(0));});
process.on("uncaughtException",() => process.exit(5));
process.on("unhandledRejection",() => process.exit(6));
