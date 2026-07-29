extends Node

# Verifies that a guard's forced retaliation aims at the OFFENDER named in a
# combat_started event, not whoever is physically nearest.
#
# Boots main.tscn, places a bystander right next to the guard and the offender
# farther away, then fires combat_started at the guard with the offender as the
# payload actor. The guard's attack_started_guard_reaction forces attack_npc.
# The buggy baseline forces it with EMPTY params, so attack_npc's NEAREST_NPC
# target_query falls back to the nearest NPC (the bystander). The fix pins the
# offender as target_npc_id so the runner targets the culprit. A self-triggered
# event (the guard's own combat echoing back) must not force any behavior.
#
# Adapted from tools/test_combat.gd::_test_guard_targets_offender (the fix
# commit's own regression test). Fail-fast checkpoints.

# Only the DISCRIMINATING assertions are scored checkpoints. Scene/state setup
# are hard preconditions (see _precondition): if one fails the run aborts and
# every scored checkpoint is emitted as failed, so a broken environment scores
# 0 rather than leaking partial credit. This keeps noop == 0 on the baseline
# (the baseline passes setup but fails the first scored checkpoint).
const CHECKPOINTS := [
	"forced_target_is_offender",
	"self_echo_forces_nothing",
]

var _frames := 0
var _done := false
var checks := []
var _aborted := false

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

# A hard precondition: not a scored checkpoint. If it fails, abort so _emit()
# marks every scored checkpoint failed (score 0, no partial credit).
func _precondition(condition: bool, detail: String) -> bool:
	if not condition:
		_aborted = true
		checks.append({"name": "precondition_failed", "pass": false, "detail": detail, "expected": "precondition holds", "actual": "failed"})
	return condition

func _emit() -> void:
	var seen := {}
	for c in checks:
		seen[c.name] = true
	for n in CHECKPOINTS:
		if not seen.has(n):
			var detail := "not reached (an earlier checkpoint failed)"
			if _aborted:
				detail = "not scored: a hard precondition failed (see precondition_failed)"
			checks.append({"name": n, "pass": false, "detail": detail, "expected": null, "actual": null})
	print(JSON.stringify({"assertions": checks}))
	get_tree().quit()

func _find_npc(npc_id: String) -> NPC:
	for node in get_tree().get_nodes_in_group("npcs"):
		var npc := node as NPC
		if npc != null and npc.profile != null and String(npc.profile.npc_id) == npc_id:
			return npc
	return null

func run_validation() -> void:
	for node in get_tree().get_nodes_in_group("npcs"):
		var n := node as NPC
		if n != null and n.brain != null:
			n.brain.set_llm_decision_enabled(false)

	var guard := _find_npc("guard")
	var offender := _find_npc("bob")
	var bystander := _find_npc("huahudie")
	# Fall back to any distinct third NPC as bystander if huahudie is absent.
	if bystander == null:
		for node in get_tree().get_nodes_in_group("npcs"):
			var n := node as NPC
			if n != null and n.profile != null and n != guard and n != offender:
				bystander = n
				break

	if not _precondition(
			guard != null and guard.brain != null and guard.event_listener != null \
				and offender != null and offender.profile != null \
				and bystander != null \
				and guard != offender and guard != bystander and offender != bystander,
			"need a guard (with brain+event_listener), offender 'bob', and a distinct bystander; got guard=%s offender=%s bystander=%s" % [guard, offender, bystander]):
		return _emit()

	# Bystander stands right next to the guard (would be NEAREST_NPC); offender is
	# farther. The event fires at the guard so it is perceivable — what matters is
	# that the forced target is the actor (offender), not whoever is closest.
	bystander.global_position = guard.global_position + Vector2(30, 0)
	offender.global_position = guard.global_position + Vector2(200, 0)

	var payload := {
		&"source": offender,
		&"actor": offender,
		&"actor_name": offender.profile.display_name,
		&"target": bystander,
		&"position": guard.global_position,
		&"involved_npcs": [offender, bystander],
		&"detail": "combat"
	}

	guard.brain.forced_behavior = null
	guard.brain.forced_params = {}
	guard.event_listener._on_game_event(&"combat_started", payload)

	if not _precondition(
			guard.brain.forced_behavior != null and String(guard.brain.forced_behavior.behavior_id) == "attack_npc",
			"guard must react to combat_started by forcing attack_npc; got %s" % (String(guard.brain.forced_behavior.behavior_id) if guard.brain.forced_behavior != null else "<none>")):
		return _emit()

	var forced_target := String(guard.brain.forced_params.get(&"target_npc_id", ""))
	if not _record("forced_target_is_offender", forced_target == "bob",
			"forced retaliation must pin the offender (bob) as target_npc_id, not fall back to nearest bystander",
			"bob", (forced_target if forced_target != "" else "<empty -> nearest NPC fallback>")):
		return _emit()

	# Self-echo: firing the guard's own combat event at itself must be skipped
	# rather than degrading into attacking the nearest person.
	guard.brain.forced_behavior = null
	guard.brain.forced_params = {}
	var self_payload := {
		&"source": guard,
		&"actor": guard,
		&"position": guard.global_position,
		&"involved_npcs": [guard, bystander],
		&"detail": "self"
	}
	guard.event_listener._on_game_event(&"combat_started", self_payload)
	_record("self_echo_forces_nothing", guard.brain.forced_behavior == null,
			"a self-triggered combat event must not force any behavior (no self-retaliation on a bystander)",
			"no forced behavior",
			(String(guard.brain.forced_behavior.behavior_id) if guard.brain.forced_behavior != null else "<none>"))
	_emit()
