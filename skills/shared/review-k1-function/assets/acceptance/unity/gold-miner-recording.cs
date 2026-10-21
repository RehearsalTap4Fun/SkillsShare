// Temporary, tagged recorder. No project asset is written; stop/exception/reload detach every callback.
var p = Newtonsoft.Json.Linq.JObject.Parse(System.Text.Encoding.UTF8.GetString(System.Convert.FromBase64String("__PARAMETERS_BASE64__")));
var tag = "FunctionPreview.Acceptance." + (string)p["recordingId"];
var output = (string)p["output"];
// workspaceRoot is supplied from this user's validated K1_REVIEW_PROFILE by the caller.
var workspace = (string)p["workspaceRoot"];
if (string.IsNullOrWhiteSpace(workspace) || string.IsNullOrWhiteSpace(output)
    || workspace.Contains("<") || !System.IO.Path.IsPathRooted(workspace) || !System.IO.Path.IsPathRooted(output))
    throw new System.InvalidOperationException("OUTPUT_SCOPE");
var boundary = System.IO.Path.GetFullPath(workspace).TrimEnd(System.IO.Path.DirectorySeparatorChar, System.IO.Path.AltDirectorySeparatorChar);
if (boundary == System.IO.Path.GetPathRoot(boundary).TrimEnd(System.IO.Path.DirectorySeparatorChar, System.IO.Path.AltDirectorySeparatorChar)
    || !System.IO.Path.GetFullPath(output).StartsWith(boundary + System.IO.Path.DirectorySeparatorChar, System.StringComparison.OrdinalIgnoreCase))
    throw new System.InvalidOperationException("OUTPUT_SCOPE");
for (var current = System.IO.Path.GetFullPath(output); !string.IsNullOrEmpty(current); current = System.IO.Path.GetDirectoryName(current)) {
    if ((System.IO.File.Exists(current) || System.IO.Directory.Exists(current))
        && (System.IO.File.GetAttributes(current) & System.IO.FileAttributes.ReparsePoint) != 0)
        throw new System.InvalidOperationException("OUTPUT_REPARSE_POINT");
}
var control = (string)p["control"];
if (control == "stop") { var stop = System.AppDomain.CurrentDomain.GetData(tag + ".stop") as System.Action; if (stop != null) stop(); return new { callbacksRemaining = System.AppDomain.CurrentDomain.GetData(tag + ".stop") == null ? 0 : 1 }; }
if (control == "status") return new { callbacksRemaining = System.AppDomain.CurrentDomain.GetData(tag + ".stop") == null ? 0 : 1 };
if (System.AppDomain.CurrentDomain.GetData(tag + ".stop") != null) throw new System.InvalidOperationException("ALREADY_RECORDING");
var view = UnityEngine.Object.FindObjectOfType<Game.GoldMiner.GoldMinerBattleView>();
if (view == null || view.Session == null) throw new System.InvalidOperationException("BATTLE_REQUIRED");
var session = view.Session;
bool OwnsSession() { return view!=null && view.Session==session && Logic.LPlayer.I.PlayerID.ToString()==(string)p["accountId"] && Logic.LPlayer.I.ServerId==(int)p["serverId"]; }
if (Logic.LPlayer.I.PlayerID.ToString() != (string)p["accountId"] || Logic.LPlayer.I.ServerId != (int)p["serverId"]) throw new System.InvalidOperationException("IDENTITY_MISMATCH");
var recorder = new NetMonitor.NetMessageRecorder();
var events = new System.Collections.Generic.List<object>();
var pending = new System.Collections.Generic.Dictionary<uint,string>();
var requestActions = new System.Collections.Generic.Dictionary<uint,string>();
int seq=0, read=0, shots=0; bool fired=false, bombed=false, hadBusy=false, closed=false;
string actionId=null, previous=null;
double started=UnityEditor.EditorApplication.timeSinceStartup;
string mode=(string)p["mode"];
void Add(string kind, string correlation, object payload, string error=null, string message=null) {
 events.Add(new { eventId=tag+":"+seq, sequence=seq++, wallTime=System.DateTime.UtcNow.ToString("o"), monotonicTime=UnityEditor.EditorApplication.timeSinceStartup,
 gameTime=session.GameTime, actionId, kind, requestCorrelation=correlation, targetIdentity=new { sessionId=System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(session).ToString() }, payload, errorCode=error, messageType=message });
}
void Frame() {
 var rt=new UnityEngine.RenderTexture(540,960,24); var prev=UnityEngine.RenderTexture.active; UnityEngine.Texture2D tex=null;
 try {
  foreach(var cam in UnityEngine.Camera.allCameras.OrderBy(c=>c.depth)) { var old=cam.targetTexture; try {cam.targetTexture=rt;cam.Render();} finally{cam.targetTexture=old;} }
  UnityEngine.RenderTexture.active=rt;tex=new UnityEngine.Texture2D(540,960,UnityEngine.TextureFormat.RGB24,false);tex.ReadPixels(new UnityEngine.Rect(0,0,540,960),0,0);tex.Apply();
  System.IO.File.WriteAllBytes(output+"-frame-"+(shots++)+".png",tex.EncodeToPNG()); Add("frame",null,new { });
 } finally { UnityEngine.RenderTexture.active=prev;if(tex!=null)UnityEngine.Object.DestroyImmediate(tex);UnityEngine.Object.DestroyImmediate(rt); }
}
UnityEditor.EditorApplication.CallbackFunction tick=null;
UnityEditor.AssemblyReloadEvents.AssemblyReloadCallback reload=null;
System.Action stopAction=null;
stopAction=()=>{
 if(closed)return;closed=true;
 UnityEditor.EditorApplication.update-=tick;
 UnityEditor.AssemblyReloadEvents.beforeAssemblyReload-=reload;
 Common.NetMonitor.MessageHookMgr.Unregister(recorder);
 System.AppDomain.CurrentDomain.SetData(tag+".stop",null);
 if(OwnsSession())view.PauseDriving();
 System.IO.File.WriteAllText(output+".json",Newtonsoft.Json.JsonConvert.SerializeObject(new { recordingId=(string)p["recordingId"], events, cleanup=new { callbacksRemaining=0 }, fired,bombed,shots },Newtonsoft.Json.Formatting.Indented));
};
reload=()=>{Add("lifecycle",null,new { hookState="domain_reload" });stopAction();};
tick=()=>{try {
 if(!UnityEditor.EditorApplication.isPlaying || !OwnsSession()){Add("lifecycle",null,new { hookState="identity_or_scene_changed" });stopAction();return;}
 foreach(var rr in recorder.Records.Skip(read).ToArray()){
  read++;if(!rr.MsgName.Contains("GoldMiner"))continue;
  var body=Newtonsoft.Json.Linq.JObject.Parse(rr.Json);string corr=null;
  if(rr.IsSend){corr=tag+":request:"+seq;pending[rr.MsgId]=corr;requestActions[rr.MsgId]=actionId;}
  else if(pending.TryGetValue(rr.MsgId,out corr)){actionId=requestActions[rr.MsgId];pending.Remove(rr.MsgId);requestActions.Remove(rr.MsgId);}
  Add(rr.IsSend?"request":"response",corr,new { income=(string)body["income"], level=(string)body["levelId"], propType=(string)body["propType"] },(string)body["errCode"],rr.MsgName);
 }
 var rig=view.Rigs[0];var hook=rig.Hook;var hs=hook.State.ToString();
 if(hs!=previous){Add("state",null,new { hookState=hs,instanceId=hook.GrabbedInstanceID,slot=rig.Slot });previous=hs;Frame();}
 if(fired && hs!="Swinging")hadBusy=true;
 if(fired && hadBusy && hs=="Swinging"){Add("lifecycle",null,new { hookState="cycle_complete" });stopAction();return;}
 if(!fired && hs=="Swinging"){
  var target=session.Elements.Values.Where(e=>!e.Consumed&&!e.HeldByHook).OrderBy(e=>UnityEngine.Mathf.Abs(hook.CurrentAngleDeg-UnityEngine.Mathf.Atan2(view.Mapper.AnchorLogicY-e.GridY-e.GridH*.5f,e.GridX+e.GridW*.5f-view.Mapper.AnchorLogicX)*UnityEngine.Mathf.Rad2Deg)).FirstOrDefault();
  if(target!=null){float angle=UnityEngine.Mathf.Atan2(view.Mapper.AnchorLogicY-target.GridY-target.GridH*.5f,target.GridX+target.GridW*.5f-view.Mapper.AnchorLogicX)*UnityEngine.Mathf.Rad2Deg;
   if(UnityEngine.Mathf.Abs(hook.CurrentAngleDeg-angle)<2){actionId=tag+":fire";Add("action",null,new { hookState="fire",instanceId=target.InstanceID });view.OnFireButton();fired=true;}}
 }
 if(mode=="bomb" && fired && !bombed && rig.Controller.CanUseBomb){actionId=tag+":bomb";Add("action",null,new { propType="bomb",instanceId=hook.GrabbedInstanceID });typeof(Game.GoldMiner.GoldMinerBattleView).GetMethod("OnPropClicked",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic).Invoke(view,new object[]{cspb.ActvGoldMinerPropType.GOLD_MINER_PROP_BOMB});bombed=true;Frame();}
 if((UnityEditor.EditorApplication.timeSinceStartup-started)*1000>(int)p["timeoutMs"] || session.GameTime>=session.LevelDurationMs-500){Add("lifecycle",null,new { hookState="step_timeout_or_level_end" });stopAction();}
 }catch(System.Exception){Add("lifecycle",null,new { hookState="exception" });stopAction();}};
Common.NetMonitor.MessageHookMgr.Register(recorder);
UnityEditor.EditorApplication.update+=tick;UnityEditor.AssemblyReloadEvents.beforeAssemblyReload+=reload;
System.AppDomain.CurrentDomain.SetData(tag+".stop",stopAction);
view.ResumeDriving();
return new { recordingId=(string)p["recordingId"], started=true, inputCoverage="normal fire/prop business entry; manual clicks unobserved" };
