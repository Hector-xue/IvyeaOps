"""套图美术指导：跨品类产品画像、动态构图、产品真值锁定与稳健回退。"""
import asyncio
import io
import json
import time

from PIL import Image, ImageDraw

from app.routers import listing as L
from app.routers.listing import visuals as V


def test_strip_json_variants():
    assert L._strip_json('```json\n{"images":[{"render_prompt":"x"}]}\n```')["images"]
    assert L._strip_json('Sure, here it is: {"a":1} done')["a"] == 1
    assert L._strip_json("not json at all") is None
    assert L._strip_json("") is None


def test_image_task_payload_is_stable_for_processing_completed_and_failed():
    assert L._parse_image_task_payload({"data": {"status": "processing"}}) == {
        "status": "processing",
    }
    assert L._parse_image_task_payload({"data": {
        "status": "completed", "result": {"images": [{"url": ["https://img/result.png"]}]},
    }}) == {"status": "completed", "provider_url": "https://img/result.png"}
    failed = L._parse_image_task_payload({"data": {"status": "failed", "message": "quota exceeded"}})
    assert failed == {"status": "failed", "error": "quota exceeded"}


def test_image_task_payload_rejects_completed_task_without_image():
    state = L._parse_image_task_payload({"data": {"status": "completed", "result": {"images": []}}})
    assert state["status"] == "failed"
    assert "URL" in state["error"]


def test_copy_result_repairs_one_malformed_bullet_instead_of_showing_raw_json():
    raw = '''{"titles":["Good title"],"bullets_a":[
["Simple Setup": Works out of the box",
"Second bullet"],"search_terms":["trail camera"]}'''
    parsed = L._parse_copy_result({"raw": raw})
    assert parsed["titles"] == ["Good title"]
    assert parsed["bullets_a"][0] == "[Simple Setup]: Works out of the box"


def test_white_background_detection_is_conservative():
    white = Image.new("RGB", (300, 300), "white")
    ImageDraw.Draw(white).rectangle((95, 55, 205, 250), fill=(25, 28, 30))
    white_bytes = io.BytesIO()
    white.save(white_bytes, "PNG")
    dark = Image.new("RGB", (300, 300), (35, 55, 42))
    dark_bytes = io.BytesIO()
    dark.save(dark_bytes, "PNG")
    assert L._white_background_score(white_bytes.getvalue())["ready"] is True
    assert L._white_background_score(dark_bytes.getvalue())["ready"] is False


def test_dense_white_background_bundle_can_touch_canvas_edges():
    bundle = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(bundle)
    # Dense multi-item bundle: one object touches the left edge and two others
    # touch top/bottom, while the true white background still spans three sides.
    draw.rectangle((0, 10, 150, 210), fill=(25, 28, 30))
    draw.rectangle((200, 0, 330, 210), fill=(35, 38, 40))
    draw.rectangle((0, 240, 100, 299), fill=(30, 32, 35))
    raw = io.BytesIO()
    bundle.save(raw, "PNG")
    result = L._white_background_score(raw.getvalue())
    assert result["border_white"] < .62
    assert result["edge_connected_white"] >= .30
    assert result["white_sides"] >= 3
    assert result["ready"] is True


def test_tightly_cropped_white_main_with_thin_margins_is_accepted():
    # 大产品占满画布、白边极薄（DE 站 Wildkamera 主图实况：overall_white 仅 ~.17、
    # border_white ~.39，但四条边线 ~90%+ 纯白且连成一片）——合法白底真值。
    tight = Image.new("RGB", (400, 340), "white")
    ImageDraw.Draw(tight).rectangle((14, 12, 386, 328), fill=(52, 60, 48))
    raw = io.BytesIO()
    tight.save(raw, "PNG")
    result = L._white_background_score(raw.getvalue())
    assert result["border_white"] < .62
    assert result["overall_white"] < .34
    assert result["ready"] is True


def test_tight_path_rejects_scene_with_one_dark_edge():
    # 场景图哪怕整体偏亮，只要有一条边不是纯白就不能进白底真值。
    scene = Image.new("RGB", (400, 340), "white")
    draw = ImageDraw.Draw(scene)
    draw.rectangle((14, 12, 386, 328), fill=(52, 60, 48))
    draw.rectangle((0, 300, 399, 339), fill=(90, 110, 80))  # 底边草地
    raw = io.BytesIO()
    scene.save(raw, "PNG")
    assert L._white_background_score(raw.getvalue())["ready"] is False


def test_cached_tight_cropped_first_gallery_image_migrates_without_redownload():
    # B0H2Z1D6R4/DE 真实存量指标：修复后 _cached_white_product_source 直接复活。
    data = {"white_product_source": "", "white_product_source_check": {"candidates": [{
        "url": "https://m.media-amazon.com/images/I/71JTUb212HL.jpg", "kind": "scraped",
        "score": 54, "border_white": .386, "overall_white": .169,
        "edge_connected_white": .167, "white_sides": 4,
        "side_white": [.896, .979, .9, .972],
    }]}}
    assert L._cached_white_product_source(data) == "https://m.media-amazon.com/images/I/71JTUb212HL.jpg"


def test_cached_sorftime_white_source_migrates_after_threshold_improvement():
    data = {"white_product_source": "", "white_product_source_check": {"candidates": [{
        "url": "https://img/white.jpg", "kind": "scraped", "score": 56,
        "border_white": .656, "overall_white": .346,
    }]}}
    assert L._cached_white_product_source(data) == "https://img/white.jpg"


def test_cached_dense_first_gallery_image_migrates_without_redownload():
    data = {"white_product_source": "", "white_product_source_check": {"candidates": [{
        "url": "https://img/dense-bundle.jpg", "kind": "scraped", "score": 36,
        "border_white": .35, "overall_white": .375,
    }]}}
    assert L._cached_white_product_source(data) == "https://img/dense-bundle.jpg"


def test_blueprint_selection_uses_product_facts_not_reference_claims():
    assert L._infer_layout_blueprint({"headline": "Wi-Fi App Control"}, 2, "detail") == "connectivity_diagram"
    assert L._infer_layout_blueprint({"headline": "No App Needed", "proof": "2.0 inch"}, 2, "detail") != "connectivity_diagram"
    assert L._infer_layout_blueprint({"headline": "0.1s Trigger Speed"}, 4, "comparison") == "speed_comparison"
    assert L._infer_layout_blueprint({"headline": "850nm Night Vision"}, 5, "lifestyle") == "day_night_split"


def test_product_visual_profiles_change_with_physical_category():
    towel = L._fallback_product_visual_profile("Organic cotton bath towel set")
    tent = L._fallback_product_visual_profile("4 person camping tent with rainfly")
    camera = L._fallback_product_visual_profile("Trail camera with screen and mounting strap")
    assert towel["object_behavior"] == "soft_goods"
    assert "folding" in towel["natural_interactions"]
    assert tent["object_behavior"] == "spatial_gear"
    assert "exact silhouette" in tent["fidelity_anchors"]
    assert camera["object_behavior"] == "rigid_device"
    assert "lens count and position" in camera["fidelity_anchors"]
    assert len({towel["scene_families"][0], tent["scene_families"][0], camera["scene_families"][0]}) == 3


def test_dynamic_prompt_inherits_category_fidelity_anchors_without_blueprint():
    profile = L._fallback_product_visual_profile("Organic cotton bath towel set")
    normalized = L._normalize_shot_plan({
        "product_profile": profile,
        "images": [
            {"shot_type": "white_main", "render_prompt": "exact source on white"},
            {"shot_type": "lifestyle", "asset_mode": "blueprint", "layout_blueprint": "use_case_mosaic",
             "render_prompt": "towel used after a shower", "evidence": "cotton towel"},
        ],
    }, 2)
    adaptive = normalized["images"][1]
    assert adaptive["asset_mode"] == "generate"
    assert adaptive["panel_prompts"] == []
    assert "border and stitching" in adaptive["render_prompt"]
    assert "soft_goods" in adaptive["render_prompt"]


def test_legacy_blueprint_migrates_to_direct_generation_without_erasing_existing_render():
    normalized = L._normalize_shot_plan({
        "product_lock": "trail camera with two lenses and mounting strap",
        "images": [
            {"shot_type": "white_main", "render_prompt": "white source"},
            {"shot_type": "hero_feature", "asset_mode": "blueprint", "layout_blueprint": "coverage_diagram",
             "render_prompt": "old fixed coverage layout", "evidence": "detection angle",
             "base_url": "/old-base.png", "final_url": "/old-final.png",
             "render_qa": {"ready": True, "score": 90}},
        ],
    }, 2)
    migrated = normalized["images"][1]
    assert migrated["asset_mode"] == "generate"
    assert migrated["layout_blueprint"] == ""
    assert migrated["final_url"] == "/old-final.png" and migrated["render_qa"]["ready"] is True
    assert normalized["product_profile"]["object_behavior"] == "rigid_device"


def test_fallback_art_direction_is_distinct_for_camera_towel_and_tent():
    row = {"asin": "B0TEST", "marketplace": "US", "copy_result": None}
    cases = [
        ("cotton bath towel set", "soft_goods", "fold"),
        ("four person camping tent", "spatial_gear", "shelter"),
        ("trail camera with mounting strap", "rigid_device", "device"),
    ]
    prompts = []
    for title, behavior, expected_word in cases:
        scrape = {"title": title, "bullets": [f"Reliable {title}"]}
        profile = L._fallback_product_visual_profile(title)
        plan = L._shot_plan_fallback(row, scrape, {}, 5, "", "gallery", profile)
        generated = [image for image in plan["images"] if image["asset_mode"] == "generate"]
        assert generated and all(image["camera_direction"] for image in generated)
        assert len({image["visual_concept"] for image in generated}) == len(generated)
        prompt = generated[0]["render_prompt"].lower()
        assert behavior in prompt and expected_word in prompt
        prompts.append(prompt)
    assert len(set(prompts)) == 3


def test_normalize_main_first_no_text_and_clamp():
    plan = {"style": {"palette": "warm"}, "product_lock": "keep shape", "images": [
        {"slot": "x", "role": "卖点", "callout": "30 Day", "text_on_image": True, "render_prompt": "a"},
        {"callout": "Waterproof", "text_on_image": True, "render_prompt": "b"},
        {"render_prompt": ""},  # 无提示词：由编译器按结构字段编译，不再丢弃
    ]}
    n = L._normalize_shot_plan(plan, 0)
    assert len(n["images"]) == 3
    assert all("[SHOT DESIGN]" in img["render_prompt"] for img in n["images"])
    m = n["images"][0]
    assert m["slot"] == "main" and m["role"] == "主图"
    assert m["text_on_image"] is False and m["callout"] is None      # main never carries text
    assert m["asset_mode"] == "generate" and m["requires_source"] is False
    assert m["size"] == "1600x1600"
    assert n["images"][1]["callout"] == "Waterproof" and n["images"][1]["text_on_image"] is True
    assert n["images"][1]["product_presence"] in ("hero", "supporting", "environmental", "absent")
    # explicit count clamps
    big = {"images": [{"render_prompt": str(i)} for i in range(12)]}
    assert len(L._normalize_shot_plan(big, 5)["images"]) == 5
    # adaptive caps at 8
    assert len(L._normalize_shot_plan(big, 0)["images"]) == 8


def test_normalize_archetypes_and_product_is_not_forced_out_of_lifestyle():
    plan = {"images": [
        {"slot": "x", "shot_type": "feature", "callout": "A", "render_prompt": "p"},
        {"shot_type": "scene", "headline": "Beautiful Footage", "render_prompt": "a landscape"},
        {"shot_type": "bogus", "render_prompt": "q"},   # invalid → defaulted
    ]}
    n = L._normalize_shot_plan(plan, 0)["images"]
    # main forced white_main, shows product, no text
    assert n[0]["shot_type"] == "white_main" and n[0]["show_product"] is True and n[0]["text_on_image"] is False
    # Legacy scene is upgraded to a real lifestyle shot.  The planner may choose
    # whether the product belongs in frame instead of forcing a generic landscape.
    assert n[1]["shot_type"] == "lifestyle" and n[1]["show_product"] is True
    assert n[1]["headline"] == "Beautiful Footage" and n[1]["text_on_image"] is True
    # invalid shot_type falls back to a valid one
    assert n[2]["shot_type"] in L._SHOT_TYPES


def test_normalize_removes_known_ai_aesthetic_prompt_habits():
    plan = {"images": [
        {"render_prompt": "Product on a futuristic platform with neon light trails and floating glass panels."},
        {"render_prompt": "Product on a real desk in window light.", "evidence": "real fact"},
        {"render_prompt": "Product detail in a real room.", "evidence": "real fact 2"},
        {"render_prompt": "Product in hand at correct scale.", "evidence": "real fact 3"},
        {"render_prompt": "Exact package contents on white.", "evidence": "real fact 4"},
    ]}
    normalized = L._normalize_shot_plan(plan, 5)
    assert "neon" not in normalized["images"][0]["render_prompt"].lower()
    assert "floating glass" not in normalized["images"][0]["render_prompt"].lower()
    assert not any(i["code"] == "ai_aesthetic" for i in normalized["quality"]["issues"])


def test_evidence_dependent_generated_proof_blocks_strategy():
    images = [{
        "render_prompt": "real product photo",
        "requires_source": True,
        "asset_mode": "generate",
        "evidence": "lab result",
    }]
    quality = L._creative_plan_quality(images, "aplus")
    assert quality["ready"] is False
    assert any(i["code"] == "missing_evidence" and i["severity"] == "error" for i in quality["issues"])


def test_generated_product_fidelity_is_a_hard_review_gate(monkeypatch):
    image = Image.new("RGB", (1600, 1600), "white")
    raw = io.BytesIO()
    image.save(raw, "PNG")

    async def fake_fetch(client, url):
        return raw.getvalue()

    async def fake_review(candidate, source, body):
        return {"available": True, "result": {
            "scores": {"product_fidelity": 81, "realism": 94, "composition": 93,
                       "typography": 96, "commercial_readiness": 92},
            "fatal_issues": [], "improvements": ["restore the exact seam layout"], "verdict": "pass",
        }}

    monkeypatch.setattr(V, "_fetch_image_bytes", fake_fetch)
    monkeypatch.setattr(V, "_render_vision_review", fake_review)
    result = asyncio.run(V.review_render_core("p", L.ReviewRenderReq(
        url="candidate", source_url="source", show_product=True,
        product_fidelity_anchors=["exact seam layout"],
    )))
    assert result["ready"] is False
    assert any(issue["code"] == "product_fidelity_failed" for issue in result["issues"])
    assert any("seam" in value for value in result["retry_guidance"])


def test_internal_review_note_can_never_become_public_proof():
    plan = {"images": [
        {"render_prompt": "white source", "proof": "Approved copy supports the sensor claim"},
        {"shot_type": "hero_feature", "render_prompt": "real studio", "headline": "Sharp in Low Light",
         "proof": "8K/30fps", "evidence": "Native 8K video"},
    ]}
    images = L._normalize_shot_plan(plan, 0)["images"]
    assert images[0]["proof"] is None
    assert images[1]["proof"] == "8K/30fps"
    assert images[1]["asset_mode"] == "generate"
    assert "only immutable product truth" in images[1]["render_prompt"].lower()


def test_adaptive_plan_does_not_inherit_collected_gallery_composition():
    plan = L._normalize_shot_plan({"images": [
        {"shot_type": "white_main", "render_prompt": "white product"},
        {"shot_type": "hero_feature", "render_prompt": "benefit scene", "evidence": "fact one",
         "asset_mode": "blueprint", "layout_blueprint": "use_case_mosaic",
         "visual_concept": "precision field hero", "camera_direction": "eye-level three quarter",
         "product_treatment": "mounted naturally"},
        {"shot_type": "lifestyle", "render_prompt": "use scene", "evidence": "fact two",
         "visual_concept": "hands-on setup", "camera_direction": "over-shoulder close view",
         "product_treatment": "handled at real scale"},
    ], "template_story": [
        {"index": 3, "role": "多场景总结", "shot_type": "lifestyle",
         "buyer_question": "有哪些使用方式？", "visual_structure": "三栏场景网格", "text_zone": "top-center"},
    ]}, 3)
    refs = ["https://img/main.jpg", "https://img/benefit.jpg", "https://img/use.jpg"]
    bound = L._bind_reference_templates(
        plan, "project-1", {"reference_images": refs, "white_product_source": refs[0]}, "gallery",
    )
    assert bound["planning_mode"] == "adaptive_direct_text"
    assert bound["template_mode"] is False
    assert bound["template_images"] == []
    assert bound["product_source_url"] == refs[0]
    assert bound["images"][0]["asset_mode"] == "generate"
    assert bound["images"][0]["source_url"] == ""
    assert bound["images"][0]["product_source_url"] == refs[0]
    assert bound["images"][1]["asset_mode"] == "generate"
    assert bound["images"][1]["product_source_url"] == refs[0]
    assert bound["images"][1]["template_url"] == ""
    assert bound["images"][2]["template_url"] == ""
    assert bound["images"][2]["shot_type"] == "lifestyle"
    assert bound["images"][2]["asset_mode"] == "generate"
    assert bound["images"][2]["role"] != "多场景总结"
    assert bound["images"][2]["text_zone"] != "top-center"
    assert bound["quality"]["ready"] is True


def test_scraped_first_image_never_becomes_product_source_and_existing_render_is_preserved():
    plan = L._normalize_shot_plan({"images": [
        {"shot_type": "white_main", "render_prompt": "white product", "source_url": "https://img/info.jpg",
         "final_url": "/old-info.png", "base_url": "/old-info.png"},
        {"shot_type": "hero_feature", "render_prompt": "benefit", "evidence": "fact",
         "asset_mode": "composite", "final_url": "/old-render.png", "base_url": "/old-render.png"},
    ]}, 2)
    bound = L._bind_reference_templates(
        plan, "project-2", {"reference_images": ["https://img/info.jpg", "https://img/scene.jpg"]}, "gallery",
    )
    assert bound["product_source_url"] == ""
    assert bound["images"][0]["source_url"] == ""
    assert bound["images"][0]["final_url"] == "/old-info.png"
    assert bound["images"][1]["asset_mode"] == "generate"
    assert bound["images"][1]["final_url"] == "/old-render.png"
    assert any(issue["code"] == "product_pending" for issue in bound["quality"]["issues"])


def test_direct_generation_uses_one_shared_product_led_colour_system_idempotently():
    plan = {
        "style": {
            "palette": "background #F5F1E8; surface #D7C9B8; supporting #7B6A58; accent #557A61; deep #252725",
            "lighting": "soft daylight from camera left",
        },
        "product_profile": L._fallback_product_visual_profile("organic cotton bath towel set"),
        "images": [
            {"render_prompt": "exact towel set on white", "visual_concept": "folded set hero",
             "camera_direction": "eye-level", "product_treatment": "soft folded stack"},
            {"shot_type": "lifestyle", "render_prompt": "towel after a shower", "evidence": "soft cotton",
             "asset_mode": "composite", "show_product": False, "visual_concept": "natural drying moment",
             "camera_direction": "shoulder-height side view", "product_treatment": "naturally draped"},
        ],
    }
    first = L._normalize_shot_plan(plan, 2)
    second = L._normalize_shot_plan(first, 2)
    assert all(image["asset_mode"] == "generate" and image["show_product"] is True for image in second["images"])
    assert all(image["render_prompt"].count("[SET COLOR SYSTEM]") == 1 for image in second["images"])
    assert all("#F5F1E8" in image["render_prompt"] and "#557A61" in image["render_prompt"] for image in second["images"])
    assert "pure #FFFFFF" in second["images"][0]["render_prompt"]
    assert "not replaced by the set palette" in second["images"][1]["render_prompt"]


def test_only_verified_white_source_can_become_product_truth(tmp_path, monkeypatch):
    uploaded = tmp_path / "user-product.jpg"
    uploaded.write_bytes(b"user-selected")
    monkeypatch.setattr(L, "IMAGES_DIR", tmp_path)
    source = L._auto_product_source("p1", {
        "uploaded_images": [str(uploaded)],
        "white_product_source": "https://img/scraped-white.jpg",
    })
    assert source == "https://img/scraped-white.jpg"
    assert L._auto_product_source("p1", {"uploaded_images": [str(uploaded)]}) == ""


def _insert_project(pid: str):
    conn = L._db()
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO listing_projects "
        "(id,asin,marketplace,status,created_at,updated_at,scrape_data,analysis_data,copy_result) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, "B0TEST", "US", "scraped", now, now,
         json.dumps({"title": "Test Bottle", "bullets": ["30 day battery", "waterproof IPX8"]}),
         json.dumps({"image_insights": "sleek matte black bottle"}),
         json.dumps({"titles": ["Test"], "bullets_a": ["30 Day Battery", "Waterproof IPX8"]})),
    )
    conn.commit()
    conn.close()


def _cleanup(pid: str):
    conn = L._db()
    conn.execute("DELETE FROM listing_projects WHERE id = ?", (pid,))
    conn.commit()
    conn.close()


def test_plan_image_set_structured(monkeypatch):
    pid = "test_shotplan_ok"
    _insert_project(pid)
    good = json.dumps({
        "style": {"palette": "warm minimal"}, "product_lock": "black bottle",
        "images": [
            {"slot": "main", "role": "主图", "render_prompt": "white bg studio bottle", "text_on_image": False},
            {"slot": "sub1", "role": "核心卖点", "callout": "30 Day Battery", "text_on_image": True,
             "text_pos": "top-right", "render_prompt": "bottle on a desk, warm light"},
        ],
    })

    captured = {}

    async def fake_call(prompt, max_tokens=2000, web_search=True):
        captured["prompt"] = prompt
        return "```json\n" + good + "\n```"

    async def fake_identity(scrape_data, product_context, white_source):
        return L._fallback_product_visual_profile("matte black rigid bottle")

    async def fake_white_source(project_id, scrape_data):
        return "https://img/white-product.jpg", [{"url": "https://img/white-product.jpg", "ready": True}]

    monkeypatch.setattr(V, "_call_ai", fake_call)
    monkeypatch.setattr(V, "_analyze_product_visual_identity", fake_identity)
    monkeypatch.setattr(V, "_detect_white_product_source", fake_white_source)
    try:
        res = asyncio.run(V.run_plan_image_set(pid, L.PlanImageSetReq(target_count=0)))
        assert res["ok"] is True and res["fallback"] is False
        imgs = res["plan"]["images"]
        assert imgs[0]["slot"] == "main" and imgs[0]["text_on_image"] is False
        assert imgs[1]["callout"] == "30 Day Battery" and imgs[1]["text_pos"] == "top-right"
        assert imgs[1]["asset_mode"] == "generate"
        assert res["plan"]["planning_mode"] == "adaptive_direct_text"
        assert res["plan"]["template_images"] == []
        assert "PRODUCT VISUAL IDENTITY" in captured["prompt"]
        assert "STRUCTURED LAYOUT BLUEPRINTS" not in captured["prompt"]
        assert "RECOMMENDED NARRATIVE SKELETON" in captured["prompt"]
        assert "product_presence" in captured["prompt"]
        assert "do not write long render prompts" in captured["prompt"].lower()
        assert "background #RRGGBB" in captured["prompt"]
        # persisted to shot_plan
        conn = L._db()
        row = conn.execute("SELECT shot_plan FROM listing_projects WHERE id = ?", (pid,)).fetchone()
        conn.close()
        assert row["shot_plan"] and "main" in row["shot_plan"]
    finally:
        _cleanup(pid)


def test_final_artwork_prompt_compiles_exact_copy_and_removes_old_textless_rules():
    plan = L._normalize_shot_plan({
        "style": {"type_system": "modern geometric sans"},
        "creative_brief": "Warm outdoor family brand; avoid technology blue",
        "language": "en",
        "images": [
            {"render_prompt": "exact product on white", "text_on_image": False},
            {
                "shot_type": "hero_feature",
                "render_prompt": (
                    "Product in a real room. Keep negative space for later typography. "
                    "Render no words, letters or numbers."
                ),
                "headline": "BUILT FOR RAIN",
                "supporting_text": "Ready in any weather",
                "proof": "IPX8",
                "text_on_image": True,
                "text_zone": "top-right",
                "evidence": "Waterproof IPX8",
            },
        ],
    }, 2)
    main, feature = plan["images"]
    assert main["text_on_image"] is False
    assert "no added marketing copy" in main["render_prompt"].lower()
    assert feature["text_on_image"] is True
    assert feature["render_prompt"].count("[FINAL ARTWORK COPY]") == 1
    assert '"headline": "BUILT FOR RAIN"' in feature["render_prompt"]
    assert '"supporting_text": "Ready in any weather"' in feature["render_prompt"]
    assert '"proof": "IPX8"' in feature["render_prompt"]
    assert "Warm outdoor family brand; avoid technology blue" in feature["render_prompt"]
    assert "requested language for added artwork copy is en" in feature["render_prompt"]
    assert "later typography" not in feature["render_prompt"].lower()
    assert "render no words" not in feature["render_prompt"].lower()

    # Re-normalizing after a manual edit replaces the copy section instead of
    # appending contradictory prompt instructions.
    feature["headline"] = "MADE FOR STORMS"
    updated = L._normalize_shot_plan(plan, 2)["images"][1]["render_prompt"]
    assert updated.count("[FINAL ARTWORK COPY]") == 1
    assert "MADE FOR STORMS" in updated and "BUILT FOR RAIN" not in updated


def test_inexact_model_rendered_copy_is_a_hard_review_gate(monkeypatch):
    image = Image.new("RGB", (1600, 1600), "white")
    raw = io.BytesIO()
    image.save(raw, "PNG")

    async def fake_fetch(client, url):
        return raw.getvalue()

    async def fake_review(candidate, source, body):
        return {"available": True, "result": {
            "scores": {"product_fidelity": 98, "realism": 92, "composition": 90,
                       "typography": 84, "copy_accuracy": 72, "commercial_readiness": 88},
            "copy_check": {"exact": False, "unexpected_copy": False,
                           "transcribed": ["BUILT FOR RA1N"]},
            "fatal_issues": [], "improvements": ["Correct the headline spelling"], "verdict": "fail",
        }}

    monkeypatch.setattr(V, "_fetch_image_bytes", fake_fetch)
    monkeypatch.setattr(V, "_render_vision_review", fake_review)
    result = asyncio.run(V.review_render_core("p", L.ReviewRenderReq(
        url="candidate", source_url="source", show_product=True,
        headline="BUILT FOR RAIN", product_fidelity_anchors=["exact silhouette"],
    )))
    assert result["ready"] is False
    assert any(issue["code"] == "artwork_copy_failed" for issue in result["issues"])
    assert any("BUILT FOR RAIN" in value for value in result["retry_guidance"])


def test_plan_image_set_fallback_on_garbage(monkeypatch):
    pid = "test_shotplan_fb"
    _insert_project(pid)

    async def fake_call(prompt, max_tokens=2000, web_search=True):
        return "I'm sorry, I can't produce that."

    monkeypatch.setattr(V, "_call_ai", fake_call)
    try:
        res = asyncio.run(V.run_plan_image_set(pid, L.PlanImageSetReq(target_count=5)))
        assert res["ok"] is True and res["fallback"] is True       # degraded, never errors
        assert len(res["plan"]["images"]) >= 1
        assert res["plan"]["images"][0]["slot"] == "main"
    finally:
        _cleanup(pid)


def test_aplus_plan_and_editor_state_persist_in_unified_creative_sets(monkeypatch):
    pid = "test_creative_sets"
    _insert_project(pid)
    payload = json.dumps({
        "style": {"direction": "restrained studio"},
        "product_lock": "same black bottle",
        "story": "benefit to proof",
        "images": [
            {
                "slot": "aplus_1", "role": "品牌首屏", "shot_type": "aplus_banner",
                "headline": "Made for Every Day", "evidence": "approved copy",
                "size": "1464x600", "render_prompt": "real bottle on a real bathroom shelf, no text",
            },
        ],
    })

    async def fake_call(prompt, max_tokens=2000, web_search=True):
        return payload

    monkeypatch.setattr(V, "_call_ai", fake_call)
    try:
        result = asyncio.run(V.run_plan_image_set(
            pid, L.PlanImageSetReq(target_count=1, deliverable="aplus"),
        ))
        assert result["plan"]["deliverable"] == "aplus"
        assert result["plan"]["images"][0]["shot_type"] == "aplus_banner"

        edited = result["plan"]
        edited["images"][0].update(
            final_url="/final.png", human_reviewed=True,
            render_qa={"ready": True, "score": 90, "issues": []},
        )
        saved = asyncio.run(L.save_creative_set(
            pid, {"deliverable": "aplus", "plan": edited}, _user="t",
        ))
        assert saved["plan"]["images"][0]["final_url"] == "/final.png"
        assert saved["plan"]["images"][0]["human_reviewed"] is True
        conn = L._db()
        row = conn.execute("SELECT creative_sets FROM listing_projects WHERE id=?", (pid,)).fetchone()
        conn.close()
        assert json.loads(row["creative_sets"])["aplus"]["images"][0]["final_url"] == "/final.png"
    finally:
        _cleanup(pid)
