# Converted from GameDevBench task_0103 test.gd.
# Emits one assertion per checkpoint ({"assertions":[...]}) with expected/actual
# so the report explains WHAT failed. Fail-fast: on the first failed checkpoint,
# the remaining (unreached) checkpoints are emitted as failed so the denominator
# stays the full checkpoint count and scores remain comparable across runs.
extends Node

const ENEMY_SCRIPT_PATH := "res://scripts/enemy.gd"
const ENEMY_TEXTURE_PATH := "res://assets/sprites/towerDefense_tile245.png"
const TARGET_TEXTURE_PATH := "res://assets/sprites/icon.svg"

# Scored checkpoints are task-discriminating only. The scene-structure checks
# (Main / NavigationRegion2D / NavigationPolygon / Enemy / CharacterBody2D /
# enemy script attached) already hold in the baseline, so they gate the run as
# silent guards but are NOT scored — otherwise a no-op leaks partial credit for
# scaffolding it never touched.
const CHECKPOINTS := [
	"navigation_node_present",
	"nav_agent_present",
	"nav_agent_corridor",
	"nav_agent_debug",
	"timer_present",
	"timer_wait_time",
	"timer_autostart",
	"timer_connected",
	"enemy_sprite_present",
	"enemy_sprite_texture",
	"enemy_shape_present",
	"enemy_shape_is_circle",
	"enemy_shape_radius",
	"target_present",
	"target_sprite_present",
	"target_sprite_texture",
	"target_sprite_scale",
	"target_shape_present",
	"target_shape_is_rect",
	"enemy_target_export",
	"script_has_pathfinding",
	"timeout_updates_target_pos",
]

var checks := []

func _ready():
	run_validation()

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

func run_validation() -> void:
	# Scaffolding guards (not scored): these all hold in the baseline. If a harness
	# broke the scene structure, bail so every task checkpoint fails (score 0).
	var main_node := get_node_or_null("Main")
	if main_node == null:
		return _emit()
	var nav_region := main_node.get_node_or_null("NavigationRegion2D")
	if not (nav_region != null and nav_region is NavigationRegion2D):
		return _emit()
	if nav_region.navigation_polygon == null:
		return _emit()
	var enemy := main_node.get_node_or_null("Enemy")
	if enemy == null:
		return _emit()
	if not (enemy is CharacterBody2D):
		return _emit()
	if not (enemy.script != null and enemy.script.resource_path == ENEMY_SCRIPT_PATH):
		return _emit()

	var nav_root := enemy.get_node_or_null("Navigation")
	if not _record("navigation_node_present", nav_root != null,
			"Enemy is missing Navigation node", "Navigation node", "null"):
		return _emit()
	var agent := nav_root.get_node_or_null("NavigationAgent2D")
	if not _record("nav_agent_present", agent != null and agent is NavigationAgent2D,
			"NavigationAgent2D missing under Enemy/Navigation", "NavigationAgent2D", "null/wrong type"):
		return _emit()
	if not _record("nav_agent_corridor",
			agent.path_postprocessing == NavigationPathQueryParameters2D.PathPostProcessing.PATH_POSTPROCESSING_CORRIDORFUNNEL,
			"NavigationAgent2D path_postprocessing must be Corridor",
			"CORRIDORFUNNEL", agent.path_postprocessing):
		return _emit()
	if not _record("nav_agent_debug", agent.debug_enabled,
			"NavigationAgent2D debug_enabled must be true", true, agent.debug_enabled):
		return _emit()

	var timer := nav_root.get_node_or_null("Timer")
	if not _record("timer_present", timer != null and timer is Timer,
			"Timer missing under Enemy/Navigation", "Timer", "null/wrong type"):
		return _emit()
	if not _record("timer_wait_time", abs(timer.wait_time - 0.1) <= 0.0001,
			"Timer wait_time must be 0.1", 0.1, timer.wait_time):
		return _emit()
	if not _record("timer_autostart", not timer.is_stopped(),
			"Timer autostart must be enabled", "running", "stopped"):
		return _emit()
	if not _record("timer_connected", timer.timeout.is_connected(Callable(enemy, "_on_timer_timeout")),
			"Timer timeout must be connected to Enemy._on_timer_timeout",
			"connected", "not connected"):
		return _emit()

	var enemy_sprite := enemy.get_node_or_null("Sprite2D")
	if not _record("enemy_sprite_present", enemy_sprite != null and enemy_sprite is Sprite2D,
			"Enemy Sprite2D missing", "Sprite2D", "null/wrong type"):
		return _emit()
	if not _record("enemy_sprite_texture",
			enemy_sprite.texture != null and enemy_sprite.texture.resource_path == ENEMY_TEXTURE_PATH,
			"Enemy Sprite2D must use towerDefense_tile245.png", ENEMY_TEXTURE_PATH,
			"null" if enemy_sprite.texture == null else enemy_sprite.texture.resource_path):
		return _emit()

	var enemy_shape := enemy.get_node_or_null("CollisionShape2D")
	if not _record("enemy_shape_present", enemy_shape != null and enemy_shape is CollisionShape2D,
			"Enemy CollisionShape2D missing", "CollisionShape2D", "null/wrong type"):
		return _emit()
	if not _record("enemy_shape_is_circle", enemy_shape.shape != null and enemy_shape.shape is CircleShape2D,
			"Enemy CollisionShape2D must be a CircleShape2D", "CircleShape2D",
			"null" if enemy_shape.shape == null else enemy_shape.shape.get_class()):
		return _emit()
	if not _record("enemy_shape_radius", abs(enemy_shape.shape.radius - 12.0) <= 0.01,
			"Enemy CircleShape2D radius must be 12", 12.0, enemy_shape.shape.radius):
		return _emit()

	var target := main_node.get_node_or_null("Target")
	if not _record("target_present", target != null and target is Area2D,
			"Target Area2D missing", "Area2D", "null/wrong type"):
		return _emit()

	var target_sprite := target.get_node_or_null("Sprite2D")
	if not _record("target_sprite_present", target_sprite != null and target_sprite is Sprite2D,
			"Target Sprite2D missing", "Sprite2D", "null/wrong type"):
		return _emit()
	if not _record("target_sprite_texture",
			target_sprite.texture != null and target_sprite.texture.resource_path == TARGET_TEXTURE_PATH,
			"Target Sprite2D must use icon.svg", TARGET_TEXTURE_PATH,
			"null" if target_sprite.texture == null else target_sprite.texture.resource_path):
		return _emit()
	if not _record("target_sprite_scale", target_sprite.scale == Vector2(0.15625, 0.15625),
			"Target Sprite2D scale must be 0.15625, 0.15625", Vector2(0.15625, 0.15625),
			target_sprite.scale):
		return _emit()

	var target_shape := target.get_node_or_null("CollisionShape2D")
	if not _record("target_shape_present", target_shape != null and target_shape is CollisionShape2D,
			"Target CollisionShape2D missing", "CollisionShape2D", "null/wrong type"):
		return _emit()
	if not _record("target_shape_is_rect", target_shape.shape != null and target_shape.shape is RectangleShape2D,
			"Target CollisionShape2D must be a RectangleShape2D", "RectangleShape2D",
			"null" if target_shape.shape == null else target_shape.shape.get_class()):
		return _emit()

	var target_path = enemy.get("target")
	if not _record("enemy_target_export", str(target_path).contains("Target"),
			"Enemy target export must point to Target", "a path containing 'Target'",
			str(target_path)):
		return _emit()

	if not _record("script_has_pathfinding",
			script_contains_required_strings(ENEMY_SCRIPT_PATH, ["get_next_path_position", "target_position", "move_and_slide"]),
			"Enemy script missing pathfinding logic",
			"get_next_path_position + target_position + move_and_slide", "missing one or more"):
		return _emit()
	var before_target_pos = agent.target_position
	enemy._on_timer_timeout()
	var expected_target_pos = target.global_position
	if not _record("timeout_updates_target_pos", agent.target_position == expected_target_pos,
			"Timer timeout must update NavigationAgent2D target_position to Target global position",
			expected_target_pos, agent.target_position):
		return _emit()

	_emit()

func script_contains_required_strings(path: String, required: Array) -> bool:
	if not FileAccess.file_exists(path):
		return false
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return false
	var text := file.get_as_text()
	file.close()
	for token in required:
		if text.find(token) == -1:
			return false
	return true
