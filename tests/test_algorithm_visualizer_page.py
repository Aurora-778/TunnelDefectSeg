from pathlib import Path


def _html() -> str:
    return Path("web_demo/index.html").read_text(encoding="utf-8")


def test_algorithm_flow_page_and_nav_exist():
    html = _html()

    assert 'data-view-link="algorithm-flow"' in html
    assert 'id="algorithm-flow"' in html
    assert 'data-app-view="algorithm-flow"' in html
    assert "算法展示 / Algorithm Flow" in html


def test_algorithm_flow_fetches_read_only_api():
    html = _html()

    assert 'fetch("/api/algorithm-events")' in html
    assert "loadAlgorithmEvents();" in html
    assert "renderAlgorithmEvents" in html


def test_algorithm_flow_boundary_copy_is_visible():
    html = _html()

    assert "基于已生成 artifacts 的算法事件流展示" in html
    assert "不运行任意代码" in html
    assert "不做模型推理" in html
    assert "不是真实在线机器人算法执行" in html


def test_algorithm_flow_missing_state_copy_is_visible():
    html = _html()

    assert "尚未生成算法展示事件流，请先运行 python scripts/generate_algorithm_events.py。" in html


def test_algorithm_flow_is_registered_in_app_view_switching():
    html = _html()

    assert '"algorithm-flow"' in html
    assert 'document.querySelectorAll("[data-app-view]")' in html
    assert 'document.querySelectorAll("[data-view-link]")' in html
