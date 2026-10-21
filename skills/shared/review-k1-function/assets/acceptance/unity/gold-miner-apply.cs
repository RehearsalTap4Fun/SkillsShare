var p = Newtonsoft.Json.Linq.JObject.Parse(System.Text.Encoding.UTF8.GetString(System.Convert.FromBase64String("__PARAMETERS_BASE64__")));
var expected = p["identity"];
var m = Game.GoldMiner.LGoldMinerMgr.I;
if ((string)p["runtimeAssembly"] != null && typeof(Game.GoldMiner.LGoldMinerMgr).Assembly.ManifestModule.ModuleVersionId.ToString() != (string)p["runtimeAssembly"])
    throw new System.InvalidOperationException("RUNTIME_ASSEMBLY_MISMATCH");
var session = m.CurrentSession == null ? "none" : System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(m.CurrentSession).ToString();
if (!Common.GameServer.IsConnected || !m.HasInitData || Logic.LPlayer.I.PlayerID.ToString() != (string)expected["accountId"]
    || Logic.LPlayer.I.ServerId != (int)expected["serverId"] || m.ActvId.ToString() != (string)expected["activityId"]
    || session != (string)expected["sessionId"]) throw new System.InvalidOperationException("IDENTITY_MISMATCH");
var action = (string)p["action"];
if (action == "heartbeat_timeout") Common.EventMgr.FireEvent(Common.TEventType.TryReconnect, ReconnectEnum.HeartBeatTimeout);
else if (action == "connection_close") {
    if (Logic.LoginMgrNew.I.IsTcpOrWebSocket) Common.GameServer.Disconnect();
    else GameServerConnection.GameServerConnection.Disconnect();
    Common.EventMgr.FireEvent(Common.TEventType.TryReconnect, ReconnectEnum.SeverBreakReally);
} else {
    var req = new cspb.GMReq();
    if (action == "stamina") req.args.Add("goldminer_addstamina");
    else if (action == "level") req.args.Add("goldminer_jumplevel");
    else if (action == "talent") req.args.Add("goldminer_settalent");
    else throw new System.InvalidOperationException("ACTION_NOT_SUPPORTED");
    req.args.Add(Logic.LPlayer.I.PlayerID.ToString());
    if (action == "stamina") req.args.Add(((int)p["amount"]).ToString());
    else if (action == "level") req.args.Add(((int)p["level"]).ToString());
    else { req.args.Add(((int)p["group"]).ToString()); req.args.Add(((int)p["level"]).ToString()); }
    Common.MessageMgr.Send(req);
}
return new { identity = expected, dispatched = action, verification = "pending_server_state" };
