extends Node

# Verifies that the rescue option/behavior is bound to the fainted (stunned)
# STATE rather than to proximity, and that going down broadcasts a world event.
# Boots main.tscn, parks a would-be rescuer 800px away from a victim, then
# knocks the victim out with the incapacitating `stunned` buff. The buggy
# baseline over-gates rescue_npc on distance (target_query max_distance=500),
# so the far rescuer never sees rescue_npc as a valid candidate, and
# _on_buff_added never emits an npc_incapacitated world event. The fix binds
# rescue availability to the stunned state (max_distance widened) and
# broadcasts npc_incapacitated when the NPC goes down.
#
# Adapted from the RESCUE-STATE half of tools/test_ws_rescue.gd (the fix
# commit's own regression test). The test's WS-backend contract/encoding half
# needs a live websocket backend and is intentionally NOT asserted here.
# Fail-fast checkpoints.

const CHECKPOINTS := [
	"scene_has_two_npcs",
	"no_rescue_before_stun",
	"victim_incapacitated_by_stun",
	"rescue_offered_regardless_of_distance",
	"npc_incapacitated_event_broadcast",
]

const RESCUE_DISTANCE := 800.0

var _frames := 0
var _done := false
var checks := []

func _ready() -> void:
	WorldClock.set_ai_paused(true)
	if get_node_or_null("Main") == null:
		add_child(load("res://main.tscn").instantiate())

func _process(_delta: float) -> void:
	_frames += 1
	if _done or _frames < 50:
		return
	_done = true
	run_validation()

func _record(name: String, condition: bool, detail: String, expected = null, actual = null) -> bool:
	checks.append({"name": name, "pass": condition, "detail": detail, "expected": expected, "actual": actual})
	return condition

func _emit() -> void:
	var seen := {}
	for c in checks:
		seen[c.name] = true
	for n in CHECKPOINTS:
		if not seen.has(n):
			checks.append({"name": n, "pass": false, "detail": "not reached (an earlier checkpoint failed)", "expected": null, "actual": null})
	print(JSON.stringify({"assertions": checks}))
	get_tree().quit()

func _find_npc(npc_id: String) -> NPC:
	for node in get_tree().get_nodes_in_group("npcs"):
		var npc := node as NPC
		if npc != null and npc.profile != null and String(npc.profile.npc_id) == npc_id:
			return npc
	return null

# A valid rescue_npc candidate is offered to `npc` via its normal planning path.
func _has_rescue_candidate(npc: NPC) -> bool:
	for entry in npc.brain._collect_valid_candidates():
		var behavior := entry.get(&"behavior", null) as BehaviorDef
		if behavior != null and behavior.behavior_id == &"rescue_npc":
			return true
	return false

func run_validation() -> void:
	for node in get_tree().get_nodes_in_group("npcs"):
		var n := node as NPC
		if n != null and n.brain != null:
			n.brain.set_llm_decision_enabled(false)
		# Clear any pre-existing stun so the only stunned NPC is our victim.
		if n != null and n.buffs != null:
			n.buffs.remove_buff(&"stunned", "test_reset")

	# Pick two distinct NPCs: a victim and a NON-PASSIVE rescuer (a passive
	# profile never collects global behaviors, so it could never see rescue_npc).
	var npcs := get_tree().get_nodes_in_group("npcs")
	var victim: NPC = null
	var rescuer: NPC = null
	for node in npcs:
		var n := node as NPC
		if n == null or n.profile == null or n.brain == null:
			continue
		if n.brain._is_passive_profile():
			continue
		if victim == null:
			victim = n
		elif rescuer == null and n != victim:
			rescuer = n
			break

	if not _record("scene_has_two_npcs",
			victim != null and rescuer != null and victim != rescuer,
			"need two distinct non-passive NPCs (victim + rescuer) with profiles",
			"2 distinct NPCs", "victim=%s rescuer=%s" % [victim, rescuer]):
		return _emit()

	# Park the rescuer far away (beyond the buggy 500px cap).
	rescuer.global_position = victim.global_position + Vector2(RESCUE_DISTANCE, 0)

	# Control: with nobody stunned, there is no rescue candidate at all.
	if not _record("no_rescue_before_stun",
			not _has_rescue_candidate(rescuer),
			"no rescue_npc candidate should exist while nobody is stunned",
			"no rescue candidate", "rescue offered with no one stunned"):
		return _emit()

	# Knock the victim out. `stunned` is an incapacitating buff.
	var events_before := EventBus.get_recent_events(50).size()
	victim.buffs.add_buff(&"stunned", "test:stunned", 60.0)
	if not _record("victim_incapacitated_by_stun",
			victim.buffs.has_buff(&"stunned"),
			"the stunned (incapacitating) buff must apply to the victim",
			"victim has stunned buff", victim.buffs.has_buff(&"stunned")):
		victim.buffs.remove_buff(&"stunned", "test_cleanup")
		return _emit()

	# DISCRIMINATING: rescue must be offered to the far rescuer purely because
	# the victim is stunned — regardless of the 800px distance. Baseline
	# max_distance=500 drops the target and offers nothing.
	if not _record("rescue_offered_regardless_of_distance",
			_has_rescue_candidate(rescuer),
			"a valid rescue_npc candidate must be offered to a distant NPC while a victim is stunned (state binding, not proximity)",
			"rescue_npc candidate present at distance %s" % RESCUE_DISTANCE,
			"no rescue candidate (over-gated by distance)"):
		victim.buffs.remove_buff(&"stunned", "test_cleanup")
		return _emit()

	# DISCRIMINATING: going down must broadcast an npc_incapacitated world event
	# (baseline never emits it, so nobody gets interrupted to come help).
	var found_event := false
	for row in EventBus.get_recent_events(50):
		if String(row.get(&"event_id", "")) == "npc_incapacitated":
			found_event = true
			break
	_record("npc_incapacitated_event_broadcast", found_event,
			"going down must broadcast an npc_incapacitated event on the EventBus",
			"npc_incapacitated event present", "not broadcast (events since stun: %d)" % (EventBus.get_recent_events(50).size() - events_before))

	victim.buffs.remove_buff(&"stunned", "test_cleanup")
	_emit()
