extends Node

# Verifies that the guard-only combat/theft event reactions are NOT broadcast to
# the whole crowd. Boots main.tscn so the DataCatalog autoload has parsed the AI
# catalog, then inspects the GLOBAL per-event reaction pool.
#
# The bug: attack_started_guard_reaction (combat_started) and theft_guard_reaction
# (steal_succeeded) are authored inline in events.json, so they land in the shared
# per-event pool and DataCatalog.get_reactions_for_event returns them for EVERY
# NPC in range — one punch triggers a bar-wide brawl. The fix adds
# EventReactionDef.is_global (default true), marks these two reactions global:false,
# and filters the global pool so only opted-in guards react.
#
# Adapted from tools/test_combat.gd::_test_guard_reaction_scope (the fix commit's
# own regression test). Fail-fast checkpoints; the global-pool checks are the
# discriminators, the profile checks degrade gracefully if a named NPC is absent.

const CHECKPOINTS := [
	"datacatalog_ready",
	"guard_reaction_not_in_global_combat_pool",
	"theft_reaction_not_in_global_theft_pool",
	"guard_profile_still_has_reaction",
	"bystander_does_not_inherit_reaction",
]

var _frames := 0
var _done := false
var checks := []

func _ready() -> void:
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

func _pool_contains(reactions, reaction_id: StringName) -> bool:
	for reaction in reactions:
		if reaction != null and reaction.reaction_id == reaction_id:
			return true
	return false

func _profile_has_reaction(npc: NPC, reaction_id: StringName) -> bool:
	if npc == null or npc.profile == null:
		return false
	for reaction in npc.profile.event_reactions:
		if reaction != null and reaction.reaction_id == reaction_id:
			return true
	return false

func run_validation() -> void:
	# DataCatalog is an autoload; confirm it has parsed reactions before asserting.
	var combat_pool = DataCatalog.get_reactions_for_event(&"combat_started")
	var theft_pool = DataCatalog.get_reactions_for_event(&"steal_succeeded")
	if not _record("datacatalog_ready",
			combat_pool != null and theft_pool != null,
			"DataCatalog.get_reactions_for_event must return the per-event reaction pools",
			"non-null pools", "combat=%s theft=%s" % [combat_pool, theft_pool]):
		return _emit()

	# DISCRIMINATOR: the guard-only combat reaction must NOT be in the global pool
	# broadcast to every NPC. Baseline leaks it here => whole crowd attacks.
	if not _record("guard_reaction_not_in_global_combat_pool",
			not _pool_contains(combat_pool, &"attack_started_guard_reaction"),
			"attack_started_guard_reaction must not be in the GLOBAL combat_started pool (else the crowd attacks)",
			"absent from global combat pool",
			"pool ids=%s" % [_pool_ids(combat_pool)]):
		return _emit()

	# DISCRIMINATOR: same for the guard-only theft reaction.
	if not _record("theft_reaction_not_in_global_theft_pool",
			not _pool_contains(theft_pool, &"theft_guard_reaction"),
			"theft_guard_reaction must not be in the GLOBAL steal_succeeded pool",
			"absent from global theft pool",
			"pool ids=%s" % [_pool_ids(theft_pool)]):
		return _emit()

	# POSITIVE: the guard must still keep the reaction via its profile opt-in.
	# Degrade gracefully if the named NPC is absent from the snapshot.
	var guard := _find_npc("guard")
	if guard == null or guard.profile == null:
		_record("guard_profile_still_has_reaction", true,
				"guard NPC not present in snapshot — positive opt-in check skipped (global-pool checks remain the discriminator)",
				"guard opted in", "guard absent")
	else:
		_record("guard_profile_still_has_reaction",
				_profile_has_reaction(guard, &"attack_started_guard_reaction"),
				"guard profile must still list attack_started_guard_reaction (guard still responds)",
				"guard has attack_started_guard_reaction",
				"guard reactions=%s" % [_profile_reaction_ids(guard)])

	# POSITIVE: a bystander must NOT inherit the guard's combat reaction.
	var bystander := _find_npc("alice")
	if bystander == null or bystander.profile == null:
		_record("bystander_does_not_inherit_reaction", true,
				"bystander NPC alice not present in snapshot — positive check skipped",
				"bystander exempt", "alice absent")
	else:
		_record("bystander_does_not_inherit_reaction",
				not _profile_has_reaction(bystander, &"attack_started_guard_reaction"),
				"bystander alice must not inherit the guard-only combat reaction",
				"alice has no attack_started_guard_reaction",
				"alice reactions=%s" % [_profile_reaction_ids(bystander)])

	_emit()

func _pool_ids(reactions) -> Array:
	var ids := []
	if reactions != null:
		for r in reactions:
			if r != null:
				ids.append(String(r.reaction_id))
	return ids

func _profile_reaction_ids(npc: NPC) -> Array:
	var ids := []
	if npc != null and npc.profile != null:
		for r in npc.profile.event_reactions:
			if r != null:
				ids.append(String(r.reaction_id))
	return ids
