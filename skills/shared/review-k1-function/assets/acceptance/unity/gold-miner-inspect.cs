var p = Newtonsoft.Json.Linq.JObject.Parse(System.Text.Encoding.UTF8.GetString(System.Convert.FromBase64String("__PARAMETERS_BASE64__")));
var m = Game.GoldMiner.LGoldMinerMgr.I;
var identity = new {
    accountId = Logic.LPlayer.I.PlayerID.ToString(), serverId = Logic.LPlayer.I.ServerId,
    activityId = m.ActvId.ToString(), clientIdentity = (string)p["identity"]["clientIdentity"],
    sessionId = m.CurrentSession == null ? "none" : System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(m.CurrentSession).ToString()
};
return new { identity, connected = Common.GameServer.IsConnected, initialized = m.HasInitData,
    state = new { stamina = m.CurrentStamina, staminaRaw = m.Stamina, staminaTs = m.StaminaTs,
        maxPassLevel = m.MaxPassLevel, unfinished = m.UnfinishedLevelID, talents = m.TalentLevels },
    runtimeAssembly = typeof(Game.GoldMiner.LGoldMinerMgr).Assembly.ManifestModule.ModuleVersionId.ToString() };
