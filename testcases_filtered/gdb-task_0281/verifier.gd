# Converted from GameDevBench task_0281 test.gd.
# Emits one assertion per checkpoint ({"assertions":[...]}) with expected/actual
# so the report explains WHAT failed. Fail-fast: on the first failed checkpoint,
# the remaining (unreached) checkpoints are emitted as failed so the denominator
# stays the full checkpoint count and scores remain comparable across runs.
extends Node

const MAIN_SCENE := preload("res://scenes/main.tscn")

# Scored checkpoints are task-discriminating only. The scene-structure checks
# (HUD/CardRow present, three cards spawned, CardStateMachine + base/clicked/
# dragging state nodes) already hold in the baseline, so they gate the run as
# silent guards but are NOT scored — otherwise a no-op leaks partial credit for
# scaffolding it never touched.
const CHECKPOINTS := [
	"enters_base_on_ready",
	"left_click_enters_clicked",
	"clicked_records_index",
	"clicked_enables_monitoring",
	"motion_enters_dragging",
	"dragging_reparents_to_overlay",
	"ignores_release_before_threshold",
	"right_click_cancels_to_base",
	"cancel_reparents_to_cardrow",
	"cancel_restores_slot",
	"base_resets_monitoring",
	"area_enter_no_duplicate_targets",
	"area_exit_removes_target",
	"release_over_playzone_removes_card",
	"play_removes_from_cardrow",
]

var checks := []

func _ready() -> void:
	await run_validation()

func _record(name: String, condition: bool, detail: String,
		expected = null, actual = null) -> bool:
	checks.append({"name": name, "pass": condition, "detail": detail,
		"expected": expected, "actual": actual})
	return condition

func _emit() -> void:
	var seen := {}
	for c in checks:
		seen[c.name] = true
	for n in CHECKPOINTS:
		if not seen.has(n):
			checks.append({"name": n, "pass": false,
				"detail": "not reached (an earlier checkpoint failed)",
				"expected": null, "actual": null})
	print(JSON.stringify({"assertions": checks}))
	get_tree().quit()

func make_mouse_button_event(button_index: MouseButton, pressed: bool) -> InputEventMouseButton:
	var event := InputEventMouseButton.new()
	event.button_index = button_index
	event.pressed = pressed
	event.position = Vector2(132, 88)
	event.global_position = event.position
	return event

func make_mouse_motion_event(position: Vector2) -> InputEventMouseMotion:
	var event := InputEventMouseMotion.new()
	event.position = position
	event.global_position = position
	return event

func _state_name(state) -> String:
	if state == null:
		return "null"
	return str(state.name) if state.get("name") != null else str(state)

func run_validation() -> void:
	var main = MAIN_SCENE.instantiate()
	add_child(main)
	await get_tree().process_frame
	await get_tree().process_frame

	# Scaffolding guards (not scored): these all hold in the baseline. If a harness
	# broke the scene structure, bail so every task checkpoint fails (score 0).
	var hand = main.get_node_or_null("HUD/CardRow")
	if hand == null:
		return _emit()
	if hand.get_child_count() != 3:
		return _emit()

	var card = hand.get_child(0)
	var machine = card.get_node_or_null("CardStateMachine")
	var base_state = card.get_node_or_null("CardStateMachine/CardBaseState")
	var clicked_state = card.get_node_or_null("CardStateMachine/CardClickedState")
	var dragging_state = card.get_node_or_null("CardStateMachine/CardDraggingState")
	var drop_area = main.get_node_or_null("PlayZone")
	var ui_layer = main.get_node_or_null("HUD")

	if machine == null:
		return _emit()
	if not (base_state != null and clicked_state != null and dragging_state != null):
		return _emit()
	if not _record("enters_base_on_ready", machine.current_state == base_state,
			"CardStateMachine must enter CardBaseState on ready",
			"CardBaseState", _state_name(machine.current_state)):
		return _emit()

	var initial_index = card.get_index()
	card._on_gui_input(make_mouse_button_event(MOUSE_BUTTON_LEFT, true))
	await get_tree().process_frame

	if not _record("left_click_enters_clicked", machine.current_state == clicked_state,
			"Left-clicking a card must enter the clicked state",
			"CardClickedState", _state_name(machine.current_state)):
		return _emit()
	if not _record("clicked_records_index", card.original_index == initial_index,
			"Clicked state must record the card's original CardRow index",
			initial_index, card.original_index):
		return _emit()
	if not _record("clicked_enables_monitoring", card.drop_point_detector.monitoring,
			"Clicked state must enable DropPointDetector.monitoring",
			true, card.drop_point_detector.monitoring):
		return _emit()

	card._input(make_mouse_motion_event(Vector2(118, 38)))
	await get_tree().process_frame

	if not _record("motion_enters_dragging", machine.current_state == dragging_state,
			"Mouse motion from the clicked state must enter the dragging state",
			"CardDraggingState", _state_name(machine.current_state)):
		return _emit()
	if not _record("dragging_reparents_to_overlay", card.get_parent() == ui_layer,
			"Dragging state must reparent the card into the overlay_layer group",
			"HUD", str(card.get_parent().name) if card.get_parent() else "null"):
		return _emit()

	card._input(make_mouse_button_event(MOUSE_BUTTON_LEFT, false))
	await get_tree().process_frame

	if not _record("ignores_release_before_threshold", machine.current_state == dragging_state,
			"Dragging must ignore release confirmation before the minimum drag threshold elapses",
			"CardDraggingState", _state_name(machine.current_state)):
		return _emit()

	card._input(make_mouse_button_event(MOUSE_BUTTON_RIGHT, true))
	await get_tree().process_frame
	await get_tree().process_frame

	if not _record("right_click_cancels_to_base", machine.current_state == base_state,
			"Right-clicking while dragging must snap the card back to the base state",
			"CardBaseState", _state_name(machine.current_state)):
		return _emit()
	if not _record("cancel_reparents_to_cardrow", card.get_parent() == hand,
			"Cancelling a drag must reparent the card back into CardRow",
			"CardRow", str(card.get_parent().name) if card.get_parent() else "null"):
		return _emit()
	if not _record("cancel_restores_slot", hand.get_child(initial_index) == card,
			"Cancelled drags must restore the card's original CardRow slot",
			"card at index %d" % initial_index, "different node"):
		return _emit()
	if not _record("base_resets_monitoring", not card.drop_point_detector.monitoring,
			"Base state must reset DropPointDetector.monitoring to false",
			false, card.drop_point_detector.monitoring):
		return _emit()

	card._on_drop_point_detector_area_entered(drop_area)
	card._on_drop_point_detector_area_entered(drop_area)
	if not _record("area_enter_no_duplicate_targets", card.targets.size() == 1,
			"DropPointDetector area enters must not duplicate targets",
			1, card.targets.size()):
		return _emit()
	card._on_drop_point_detector_area_exited(drop_area)
	if not _record("area_exit_removes_target", card.targets.is_empty(),
			"DropPointDetector area exits must remove the target",
			0, card.targets.size()):
		return _emit()

	card._on_gui_input(make_mouse_button_event(MOUSE_BUTTON_LEFT, true))
	await get_tree().process_frame
	card._input(make_mouse_motion_event(Vector2(118, 38)))
	await get_tree().process_frame
	card._on_drop_point_detector_area_entered(drop_area)
	await get_tree().create_timer(0.05).timeout
	card._input(make_mouse_button_event(MOUSE_BUTTON_LEFT, false))
	await get_tree().process_frame
	await get_tree().process_frame

	if not _record("release_over_playzone_removes_card", not is_instance_valid(card),
			"Releasing over PlayZone after the drag threshold must play and remove the card",
			"card removed", "card still valid" if is_instance_valid(card) else "removed"):
		return _emit()
	if not _record("play_removes_from_cardrow", hand.get_child_count() == 2,
			"Playing a card must remove it from CardRow", 2, hand.get_child_count()):
		return _emit()

	_emit()
