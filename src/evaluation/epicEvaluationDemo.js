const SAME_ORIGIN = typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:8000";
const API_BASE = (import.meta.env.VITE_API_BASE_URL || import.meta.env.VITE_BACKEND_URL || SAME_ORIGIN).replace(/\/$/, "");
async function requestJson(path, options={}) {
  const response=await fetch(`${API_BASE}${path}`,{credentials:"include",headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
  const payload=await response.json().catch(()=>null);
  if(!response.ok) throw new Error(payload?.detail||payload?.message||`Epic evaluation request failed: ${response.status}`);
  return payload;
}
export function getEpicEvaluationBootstrap(){ return requestJson('/api/epic-evaluation-demo/bootstrap'); }
export function startEpicEvaluationDemo(waveformSessionId){
  const id=String(waveformSessionId||'').trim();
  if(!id) return Promise.reject(new Error('A waveform session ID is required to start the Epic evaluation.'));
  return requestJson('/api/epic-evaluation-demo/start',{method:'POST',body:JSON.stringify({waveformSessionId:id})});
}
export function getEpicEvaluationDemoStatus(waveformSessionId){ return requestJson(`/api/epic-evaluation-demo/status/${encodeURIComponent(String(waveformSessionId||'').trim())}`); }
export function cancelEpicEvaluationDemo(waveformSessionId){ return requestJson(`/api/epic-evaluation-demo/cancel/${encodeURIComponent(String(waveformSessionId||'').trim())}`,{method:'POST'}); }
