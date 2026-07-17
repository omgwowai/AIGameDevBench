extends Node

# Verifies that NPCBrain._apply_llm_result gates persistent LLM side effects
# (favorability / goal board / memories) on response validity.
#   - A BAD response (a sequence that parses but names no real behavior) that also
#     smuggles favorability/goal/memory fields must NOT mutate any persistent state
#     before bouncing to the local fallback. The buggy baseline commits all three
#     before validating the sequence, so it pollutes the NPC permanently.
#   - A WAIT response (a first-class decision) carrying side effects MUST apply them.
#   - A NORMAL response with a real sequence applies side effects as before.
#
# Adapted from tools/test_llm_sideeffect_gating.gd (the fix commit's own regression
# test). Fail-fast checkpoints.

const CHECKPOINTS := [
	"scene_has_bob_and_alice",
	"bad_response_leaves_favorability_unchanged",
	"bad_response_leaves_goal_unchanged",
	"bad_response_leaves_memory_unchanged",
	"wait_response_applies_favorability",
	"wait_response_applies_goal",
	"normal_response_applies_favorability",
]

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

func _memory_note(npc: NPC, object_type: StringName, object_id: StringName) -> String:
	# perception_memory is keyed "type:id" (one entry per key, overwritten). Empty when absent.
	var key := "%s:%s" % [String(object_type), String(object_id)]
	var entry = npc.perception_memory.get(key, null)
	if entry is Dictionary:
		return String((entry as Dictionary).get("feel", (entry as Dictionary).get("note", "")))
	return ""

func _first_self_behavior_id() -> String:
	# A behavior with no target requirement is safe to "decide" without a target.
	for bid in ["wander", "rest", "idle", "wait_around"]:
		if DataCatalog.get_behavior(StringName(bid)) != null:
			return bid
	return ""

func run_validation() -> void:
	for node in get_tree().get_nodes_in_group("npcs"):
		var n := node as NPC
		if n != null and n.brain != null:
			n.brain.set_llm_decision_enabled(false)

	var actor := _find_npc("bob")
	var other := _find_npc("alice")
	if not _record("scene_has_bob_and_alice",
			actor != null and other != null and actor.profile != null and other.profile != null
				and actor.brain != null,
			"need bob + alice with profiles and a brain",
			"bob & alice present", "bob=%s alice=%s" % [actor, other]):
		return _emit()

	var aid := StringName(actor.profile.npc_id)
	var oid := StringName(other.profile.npc_id)

	# ---------- Case 1: BAD response must not pollute persistent state ----------
	var fav_before := RelationshipService.get_favorability(aid, oid)
	var goal_before := actor.profile.long_term_goal
	var mem_before := _memory_note(actor, &"behavior", &"general")

	# Sequence is present but unparseable (no real behavior_id), yet it smuggles
	# favorability + goal + memory. The buggy baseline writes all three.
	var bad_result := {
		"fast_response": {
			"sequence": [{"behavior_id": "___not_a_real_behavior___"}],
			"favorability_changes": [{"target_npc_id": String(oid), "delta": -7.0}],
			"goal_board": {"goal": "polluted_goal_SHOULD_NOT_STICK", "status": "active"},
			"memories": [{"about": "general", "note": "polluted_memory_SHOULD_NOT_STICK"}]
		}
	}
	actor.brain._apply_llm_result(bad_result, &"test_bad", false, &"test_invalid")

	var fav_after := RelationshipService.get_favorability(aid, oid)
	var goal_after := actor.profile.long_term_goal
	var mem_after := _memory_note(actor, &"behavior", &"general")

	if not _record("bad_response_leaves_favorability_unchanged",
			is_equal_approx(fav_after, fav_before),
			"an invalid response (no real behavior) must not mutate favorability before fallback",
			"favorability == %.2f" % fav_before, fav_after):
		return _emit()
	if not _record("bad_response_leaves_goal_unchanged",
			goal_after == goal_before,
			"an invalid response must not overwrite the goal board before fallback",
			goal_before, goal_after):
		return _emit()
	if not _record("bad_response_leaves_memory_unchanged",
			mem_after == mem_before,
			"an invalid response must not write memory before fallback",
			("<empty>" if mem_before == "" else mem_before), ("<empty>" if mem_after == "" else mem_after)):
		return _emit()

	# ---------- Case 2: WAIT response still applies side effects ----------
	var fav_before2 := RelationshipService.get_favorability(aid, oid)
	var wait_result := {
		"fast_response": {
			"sequence": ["wait"],
			"favorability_changes": [{"target_npc_id": String(oid), "delta": 3.0}],
			"goal_board": {"goal": "goal_set_while_waiting_SHOULD_STICK", "status": "active"}
		}
	}
	actor.brain._apply_llm_result(wait_result, &"test_wait", false, &"test_invalid")

	var fav_after2 := RelationshipService.get_favorability(aid, oid)
	var goal_after2 := actor.profile.long_term_goal
	if not _record("wait_response_applies_favorability",
			is_equal_approx(fav_after2, fav_before2 + 3.0),
			"a wait response (valid) must still apply its favorability change",
			"favorability == %.2f" % (fav_before2 + 3.0), fav_after2):
		return _emit()
	if not _record("wait_response_applies_goal",
			goal_after2 == "goal_set_while_waiting_SHOULD_STICK",
			"a wait response (valid) must still apply its goal board",
			"goal_set_while_waiting_SHOULD_STICK", goal_after2):
		return _emit()

	# ---------- Case 3: NORMAL response with a real sequence applies side effects ----------
	var real_behavior := _first_self_behavior_id()
	if real_behavior == "":
		# No target-less behavior in the catalog: mark the checkpoint reached but
		# not applicable rather than failing on environment shape.
		_record("normal_response_applies_favorability", true,
				"(no self behavior in catalog; normal-response sub-check skipped)",
				"skipped", "skipped")
		return _emit()

	var fav_before3 := RelationshipService.get_favorability(aid, oid)
	var good_result := {
		"fast_response": {
			"sequence": [{"behavior_id": real_behavior}],
			"favorability_changes": [{"target_npc_id": String(oid), "delta": 1.0}]
		}
	}
	actor.brain._apply_llm_result(good_result, &"test_good", false, &"test_invalid")
	var fav_after3 := RelationshipService.get_favorability(aid, oid)
	_record("normal_response_applies_favorability",
			is_equal_approx(fav_after3, fav_before3 + 1.0),
			"a normal response with a real behavior must apply its favorability change",
			"favorability == %.2f" % (fav_before3 + 1.0), fav_after3)
	_emit()
