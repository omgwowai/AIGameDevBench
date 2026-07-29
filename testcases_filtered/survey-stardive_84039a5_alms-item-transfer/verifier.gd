extends Node

# Verifies that when one NPC begs and another gives alms, the alms item actually
# transfers from the giver's inventory to the beggar's.
#
# A directed give_alms candidate carries a LIVE beggar NPC reference in its
# params; that object cannot survive the LLM JSON round-trip. The fix stashes the
# beggar context at candidate-build time (_append_directed_broadcast_candidates)
# and merges it back when the chosen give_alms behavior starts (_pop_sequence_entry
# -> _merge_alms_context_params). The buggy baseline never restores it, so
# _complete_give_alms() finds no beggar (_pending_alms_beggar() == null), transfers
# nothing, and emits no alms_given event.
#
# This verifier boots main.tscn, has the beggar broadcast an awaiting_alms request
# directed at the giver, drives the real build -> pop -> complete pipeline with a
# give_alms sequence entry that (like a real LLM reply) carries NO beggar param,
# then asserts the coin moved and the alms_given event involves the beggar.
#
# Adapted from tools/test_alms_transfer.gd (the fix commit's own regression test).
# Fail-fast checkpoints. Version-agnostic: never names fix-only symbols so the
# buggy baseline runs and fails on the observable transfer rather than crashing.

# Only the DISCRIMINATING assertions are scored checkpoints. Scene/state setup
# are hard preconditions (see _precondition): if one fails the run aborts and
# every scored checkpoint is emitted as failed, so a broken environment scores
# 0 rather than leaking partial credit. This keeps noop == 0 on the baseline
# (the baseline passes setup but fails the first scored checkpoint: the item
# never transfers).
const CHECKPOINTS := [
	"alms_item_transferred_to_beggar",
	"alms_given_event_involves_beggar",
]

var _frames := 0
var _done := false
var checks := []
var _events: Array[Dictionary] = []
var _aborted := false

func _ready() -> void:
	WorldClock.set_ai_paused(true)
	if get_node_or_null("Main") == null:
		add_child(load("res://main.tscn").instantiate())
	EventBus.game_event.connect(func(eid, payload): _events.append({"id": String(eid), "payload": payload}))

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

func _find_event(id: String) -> Dictionary:
	for e in _events:
		if String(e.get("id", "")) == id:
			return e
	return {}

# The offline snapshot pins commit 1d83e11, where scripts/resources/item_def.gd
# is missing the `held_buff_ids` property that DataCatalog.gd assigns during
# _item_from_config (that property was an uncommitted working-tree change at the
# time, committed only later). The bad assignment aborts _load_from_config(), so
# the runtime silently falls back to builtin defaults that lack the give_alms /
# awaiting_alms catalog entries. That snapshot/code inconsistency is unrelated to
# the alms-transfer bug under test, so re-register the real buff/behavior defs
# straight from the config JSON (bypassing the broken item loader). This is
# identical for the buggy baseline and the golden fix — it only restores the
# catalog data both versions rely on.
func _ensure_alms_catalog() -> void:
	if DataCatalog.get_buff(&"awaiting_alms") == null:
		for row in DataCatalog._load_json_array("res://configs/ai_catalog/buffs.json"):
			if row is Dictionary:
				var buff = DataCatalog._buff_from_config(row)
				DataCatalog.buffs[buff.buff_id] = buff
	if DataCatalog.get_behavior(&"give_alms") == null:
		for row in DataCatalog._load_json_array("res://configs/ai_catalog/behaviors.json"):
			if row is Dictionary:
				var behavior = DataCatalog._behavior_from_config(row)
				DataCatalog.behaviors[behavior.behavior_id] = behavior

func run_validation() -> void:
	_ensure_alms_catalog()
	for node in get_tree().get_nodes_in_group("npcs"):
		var n := node as NPC
		if n != null and n.brain != null:
			n.brain.set_llm_decision_enabled(false)

	var beggar := _find_npc("lazybones")
	var giver := _find_npc("bob")
	# Fall back to any two distinct NPCs with the needed components.
	if beggar == null or giver == null:
		var distinct: Array = []
		for node in get_tree().get_nodes_in_group("npcs"):
			var n := node as NPC
			if n != null and n.profile != null and n.buffs != null and n.inventory != null and n.brain != null and n.runner != null:
				distinct.append(n)
		if distinct.size() >= 2:
			if giver == null:
				giver = distinct[0]
			for cand in distinct:
				if cand != giver:
					beggar = cand
					break
	if not _precondition(
			beggar != null and giver != null and beggar != giver
				and beggar.buffs != null and beggar.inventory != null
				and giver.inventory != null and giver.brain != null and giver.runner != null,
			"need a distinct beggar and giver with buffs/inventory/brain/runner; got beggar=%s giver=%s" % [beggar, giver]):
		return _emit()

	# Stock the giver with coins to hand over; note the beggar's starting count.
	giver.inventory.set_count(&"coin", 5)
	var giver_before: int = giver.inventory.get_count(&"coin")
	var beggar_before: int = beggar.inventory.get_count(&"coin")
	if not _precondition(giver_before >= 1,
			"giver must hold at least one coin to give as alms (giver coin=%s)" % giver_before):
		return _emit()

	# Beggar broadcasts an awaiting_alms request directed at the giver.
	giver.brain._pending_behavior_sequence = []
	beggar.buffs.remove_buff(&"awaiting_alms", "test_reset")
	beggar.buffs.add_buff(&"awaiting_alms", "test:beg", -999.0, {
		&"target_npc": giver,
		&"speech": "行行好赏两个钱吧！"
	})
	var has_req := false
	for row in beggar.buffs.get_active_buff_rows():
		if String(row.get(&"buff_id", "")) == "awaiting_alms":
			has_req = true
	if not _precondition(has_req,
			"beggar must carry an active awaiting_alms buff directed at the giver (awaiting_alms active=%s)" % has_req):
		beggar.buffs.remove_buff(&"awaiting_alms", "test_cleanup")
		return _emit()

	# Build the giver's directed candidates (the fix stashes the live beggar here).
	var candidates: Array[Dictionary] = []
	var seen := {}
	giver.brain._append_directed_broadcast_candidates(candidates, seen)
	var offers_give_alms := false
	for c in candidates:
		var b := c.get(&"behavior", null) as BehaviorDef
		if b != null and b.behavior_id == &"give_alms" and c.get(&"target", null) == beggar:
			offers_give_alms = true
	if not _precondition(offers_give_alms,
			"the giver must see a give_alms candidate directed at the begging NPC (candidates=%d)" % candidates.size()):
		beggar.buffs.remove_buff(&"awaiting_alms", "test_cleanup")
		return _emit()

	# The LLM returns a give_alms step with NO beggar param (a live NPC object can
	# never round-trip JSON) — only model-authored params.
	giver.brain._pending_behavior_sequence = [{
		&"behavior_id": &"give_alms",
		&"params": {&"item_id": "coin", &"speech": "拿去吧。"}
	}]
	var entry := giver.brain._pop_sequence_entry()
	var merged_params: Dictionary = entry.get(&"params", {})
	if not _precondition(
			(entry.get(&"behavior", null) as BehaviorDef) != null
				and (entry.get(&"behavior", null) as BehaviorDef).behavior_id == &"give_alms"
				and String(merged_params.get(&"item_id", merged_params.get("item_id", ""))) == "coin",
			"a give_alms behavior entry (LLM item_id preserved) must be ready to run; behavior=%s keys=%s" % [entry.get(&"behavior", null), merged_params.keys()]):
		beggar.buffs.remove_buff(&"awaiting_alms", "test_cleanup")
		return _emit()

	# Run the actual completion with the merged params.
	_events.clear()
	giver.runner.current_behavior = DataCatalog.get_behavior(&"give_alms")
	giver.runner.current_target = beggar
	giver.runner.current_params = merged_params
	giver.runner._complete_give_alms()

	var beggar_after: int = beggar.inventory.get_count(&"coin")
	var giver_after: int = giver.inventory.get_count(&"coin")
	# DISCRIMINATOR: baseline never merges the beggar back, so nothing transfers.
	if not _record("alms_item_transferred_to_beggar",
			beggar_after == beggar_before + 1 and giver_after == giver_before - 1,
			"the alms item must move from giver to beggar (giver -1, beggar +1)",
			"beggar %d->%d, giver %d->%d" % [beggar_before, beggar_before + 1, giver_before, giver_before - 1],
			"beggar %d->%d, giver %d->%d" % [beggar_before, beggar_after, giver_before, giver_after]):
		beggar.buffs.remove_buff(&"awaiting_alms", "test_cleanup")
		return _emit()

	var alms_evt := _find_event("alms_given")
	var involves_beggar := false
	if not alms_evt.is_empty():
		var involved = alms_evt.get("payload", {}).get(&"involved_npcs", [])
		involves_beggar = involved is Array and involved.has(beggar)
	_record("alms_given_event_involves_beggar", involves_beggar,
			"an alms_given event involving the beggar must broadcast the gift",
			"alms_given involving beggar", "event=%s involves=%s" % [not alms_evt.is_empty(), involves_beggar])
	beggar.buffs.remove_buff(&"awaiting_alms", "test_cleanup")
	_emit()
