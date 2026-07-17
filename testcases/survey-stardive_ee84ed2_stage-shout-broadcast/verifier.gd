extends Node

# Verifies that a stage performance is an attention-grabbing WORLD EVENT: when a
# performer starts a show (and when it shouts for tips), a game event is
# broadcast and nearby bystanders are interrupted into a rethink so the show is
# not silently ignored.
#
# Boots main.tscn, pauses AI, drives a bystander into a low-priority `wander`,
# then drives a performer into `perform_on_stage`'s INTERACTING phase via the
# shared _start_interaction() entry point. On the buggy baseline nothing is
# emitted, so the bystander keeps wandering and is never flagged to rethink. The
# fix broadcasts `performance_started` / `performance_shout` (behavior_runner.gd)
# plus events.json reactions (EVENT_DISTANCE_LESS_THAN 600, interrupt_priority
# 50) that interrupt the bystander.
#
# Adapted from tools/test_stage_show.gd (the fix commit's own regression test):
# "the performance_started event must interrupt [a nearby bystander] into a
# rethink". Fail-fast checkpoints.

const CHECKPOINTS := [
	"scene_has_two_npcs",
	"bystander_running_low_priority_behavior",
	"performance_start_broadcasts_world_event",
	"bystander_interrupted_to_rethink",
	"shout_broadcasts_world_event",
]

var _frames := 0
var _done := false
var checks := []
var _seen_events := {}

func _ready() -> void:
	WorldClock.set_ai_paused(true)
	if get_node_or_null("Main") == null:
		add_child(load("res://main.tscn").instantiate())
	if not EventBus.game_event.is_connected(_on_game_event):
		EventBus.game_event.connect(_on_game_event)

func _on_game_event(event_id: StringName, _payload: Dictionary) -> void:
	_seen_events[String(event_id)] = true

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

func _find_area(area_id: StringName) -> BehaviorArea:
	for node in get_tree().get_nodes_in_group("behavior_areas"):
		var area := node as BehaviorArea
		if area != null and area.area_id == area_id:
			return area
	return null

func _reset_npc(npc: NPC) -> void:
	if npc == null or npc.runner == null:
		return
	if npc.runner.current_behavior != null:
		npc.runner.interrupt(&"test_reset")

func run_validation() -> void:
	for node in get_tree().get_nodes_in_group("npcs"):
		var n := node as NPC
		if n != null and n.brain != null:
			n.brain.set_llm_decision_enabled(false)

	var npcs := get_tree().get_nodes_in_group("npcs")
	var distinct: Array = []
	for node in npcs:
		var n := node as NPC
		if n != null and n.brain != null and n.runner != null and not distinct.has(n):
			distinct.append(n)
	var stage_area := _find_area(&"stage_area")
	var perform := DataCatalog.get_behavior(&"perform_on_stage")
	var wander := DataCatalog.get_behavior(&"wander")
	if not _record("scene_has_two_npcs",
			distinct.size() >= 2 and stage_area != null and perform != null and wander != null,
			"need two distinct NPCs, the stage_area, and perform_on_stage / wander behaviors",
			"2 NPCs + stage_area + behaviors",
			"npcs=%d stage_area=%s perform=%s wander=%s" % [distinct.size(), stage_area, perform, wander]):
		return _emit()

	var performer := distinct[0] as NPC
	var bystander := distinct[1] as NPC
	_reset_npc(performer)
	_reset_npc(bystander)

	# Bystander idles nearby on a low-priority, interruptible behavior.
	performer.global_position = stage_area.global_position
	bystander.global_position = stage_area.global_position + Vector2(-160, 0)
	bystander.runner.start_behavior(wander)
	if not _record("bystander_running_low_priority_behavior",
			bystander.runner.get_current_behavior_id() == &"wander"
				and bystander.runner.current_behavior != null
				and bystander.runner.current_behavior.can_be_interrupted,
			"bystander must be running an interruptible low-priority behavior",
			"wander running & interruptible",
			"id=%s interruptible=%s" % [
				String(bystander.runner.get_current_behavior_id()),
				(bystander.runner.current_behavior.can_be_interrupted if bystander.runner.current_behavior != null else "null")]):
		return _emit()

	# Drive the performer into the INTERACTING phase of perform_on_stage via the
	# shared entry point. On the fix this fires _announce_performance_started().
	bystander.brain.rethink_queued = false
	bystander.runner.last_interrupt_reason = &""
	_seen_events.clear()
	performer.runner.current_behavior = perform
	performer.runner.current_params = {&"tip_speech": "各位捧个场，赏几个铜板吧！"}
	performer.runner.current_target = stage_area
	performer.runner._start_interaction()

	if not _record("performance_start_broadcasts_world_event",
			_seen_events.has("performance_started"),
			"starting a show must broadcast a performance_started world event",
			"performance_started emitted", "events seen=%s" % [_seen_events.keys()]):
		return _emit()

	if not _record("bystander_interrupted_to_rethink",
			String(bystander.runner.last_interrupt_reason).find("performance") >= 0
				and bystander.brain.rethink_queued,
			"the performance_started event must interrupt a nearby bystander into a rethink",
			"interrupt reason contains 'performance' & rethink queued",
			"last_interrupt=%s rethink_queued=%s" % [
				String(bystander.runner.last_interrupt_reason), bystander.brain.rethink_queued]):
		return _emit()

	# Shouting for tips is itself a world event so passers-by hear the pitch.
	_seen_events.clear()
	performer.runner._beg_for_tips([], "表演者")
	_record("shout_broadcasts_world_event",
			_seen_events.has("performance_shout"),
			"shouting for tips must broadcast a performance_shout world event",
			"performance_shout emitted", "events seen=%s" % [_seen_events.keys()])
	_emit()
