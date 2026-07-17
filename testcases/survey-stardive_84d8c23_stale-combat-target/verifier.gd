extends Node

# Verifies an explicit target_npc_id beats a stale stashed opponent in combat.
# Boots main.tscn, puts the attacker in_combat with a stale opponent (stashing
# opponent=stale), then feeds the brain a behavior whose params name a DIFFERENT
# victim via target_npc_id. The buggy baseline leaks the stale opponent into the
# merged params and _combat_opponent() returns it, so the attack hits the wrong
# NPC. The fix drops the stash on an explicit target and hits the named victim.
#
# Adapted from tools/test_combat.gd::_test_explicit_target_overrides_stale_opponent
# (the fix commit's own regression test). Fail-fast checkpoints.

const CHECKPOINTS := [
	"scene_has_three_npcs",
	"stale_opponent_stashed",
	"stale_opponent_not_leaked_into_params",
	"combat_opponent_resolves_named_victim",
	"attack_hits_named_victim",
	"stale_opponent_untouched",
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

func _reset_combat(a: NPC, b: NPC) -> void:
	for npc in [a, b]:
		if npc != null and npc.buffs != null:
			for id in [&"in_combat", &"pleading", &"awaiting_plea", &"flee_immunity", &"stunned", &"hurt_heavy", &"hurt_light", &"hurt_flesh"]:
				npc.buffs.remove_buff(id, "test_reset")

func run_validation() -> void:
	for node in get_tree().get_nodes_in_group("npcs"):
		var n := node as NPC
		if n != null and n.brain != null:
			n.brain.set_llm_decision_enabled(false)

	var attacker := _find_npc("bob")
	var victim := _find_npc("alice")
	var stale := _find_npc("guard")
	# Fall back to any three distinct NPCs if the named ones are absent.
	if attacker == null or victim == null or stale == null:
		var npcs := get_tree().get_nodes_in_group("npcs")
		var distinct: Array = []
		for node in npcs:
			var n := node as NPC
			if n != null and n.profile != null and not distinct.has(n):
				distinct.append(n)
		if distinct.size() >= 3:
			attacker = distinct[0]; victim = distinct[1]; stale = distinct[2]
	if not _record("scene_has_three_npcs",
			attacker != null and victim != null and stale != null and attacker != victim and victim != stale and attacker != stale,
			"need three distinct NPCs (attacker/victim/stale opponent) with profiles",
			"3 distinct NPCs", "attacker=%s victim=%s stale=%s" % [attacker, victim, stale]):
		return _emit()

	_reset_combat(attacker, victim)
	_reset_combat(attacker, stale)

	# attacker already in combat with `stale` => in_combat candidate stashes opponent=stale.
	attacker.runner._apply_in_combat_state(attacker, stale)
	var candidates: Array[Dictionary] = []
	var seen := {}
	attacker.brain._pending_combat_context_params = {}
	attacker.brain._append_directed_broadcast_candidates(candidates, seen)
	if not _record("stale_opponent_stashed",
			attacker.brain._pending_combat_context_params.get(&"opponent") == stale,
			"in_combat broadcast must stash the current opponent for the round-trip",
			"stash opponent==stale", attacker.brain._pending_combat_context_params.get(&"opponent")):
		attacker.buffs.remove_buff(&"in_combat", "test_cleanup")
		return _emit()

	# LLM now decides to attack the OTHER NPC by explicit id.
	var victim_id := String(victim.profile.npc_id)
	attacker.brain._pending_behavior_sequence = [{
		&"behavior_id": &"attack_npc",
		&"params": {&"target_npc_id": victim_id, &"speech": "看招！"}
	}]
	var entry := attacker.brain._pop_sequence_entry()
	var merged: Dictionary = entry.get(&"params", {})
	if not _record("stale_opponent_not_leaked_into_params",
			not (merged.has(&"opponent") or merged.has("opponent")),
			"an explicit target_npc_id must drop the stale stashed opponent from merged params",
			"no 'opponent' key in merged params", "merged keys=%s" % [merged.keys()]):
		attacker.buffs.remove_buff(&"in_combat", "test_cleanup")
		return _emit()

	stale.needs.set_need(&"health", 100.0)
	victim.needs.set_need(&"health", 100.0)
	var stale_before: float = stale.needs.get_need(&"health")
	var victim_before: float = victim.needs.get_need(&"health")
	attacker.runner.start_behavior(entry.get(&"behavior"), merged)
	var opp := attacker.runner._combat_opponent()
	if not _record("combat_opponent_resolves_named_victim", opp == victim,
			"_combat_opponent must resolve the LLM's named victim, not the stale opponent",
			String(victim.name), (String(opp.name) if opp != null else "null")):
		attacker.buffs.remove_buff(&"in_combat", "test_cleanup")
		return _emit()

	attacker.runner._complete_attack()
	if not _record("attack_hits_named_victim", victim.needs.get_need(&"health") < victim_before,
			"the attack must reduce the named victim's health",
			"victim health < %s" % victim_before, victim.needs.get_need(&"health")):
		attacker.buffs.remove_buff(&"in_combat", "test_cleanup")
		return _emit()

	_record("stale_opponent_untouched", stale.needs.get_need(&"health") >= stale_before,
			"the stale opponent must not be hit by the explicitly-targeted attack",
			"stale health unchanged (>= %s)" % stale_before, stale.needs.get_need(&"health"))
	attacker.buffs.remove_buff(&"in_combat", "test_cleanup")
	_emit()
