#!/usr/bin/env python3
"""Test per ctx-vision: correzione ctx_model al modello vision quando il body
contiene immagini in provider GLM.

Il bug: ctx_model veniva impostato su glm-5.3 (1M token) ma il body con immagini
veniva poi dirottato su glm-4.6V (131k) da route_image_to_vision(). Il gate
decideva "215k < 1M, ok" ma 215k > 131k -> overflow + loop-breaker.
"""

import json
import sys
sys.path.insert(0, "src")

from glm_backend import body_has_image, route_image_to_vision, GLM_VISION_MODEL


def make_body(with_image: bool = False, text_tokens: int = 5000) -> bytes:
    content = [{"type": "text", "text": "x" * text_tokens}]
    if with_image:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}
        })
    return json.dumps({
        "model": "glm-5.3",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 4096
    }).encode()


def test_body_has_image_true():
    assert body_has_image(make_body(with_image=True)) is True

def test_body_has_image_false():
    assert body_has_image(make_body(with_image=False)) is False

def test_body_has_image_empty():
    assert body_has_image(b"{}") is False

def test_body_has_image_invalid_json():
    assert body_has_image(b"not json") is False

def test_route_image_to_vision_with_image():
    body = make_body(with_image=True)
    result = route_image_to_vision("glm-5.3", body)
    assert result == GLM_VISION_MODEL, f"Atteso {GLM_VISION_MODEL}, ottenuto {result}"

def test_route_image_to_vision_no_image():
    body = make_body(with_image=False)
    result = route_image_to_vision("glm-5.3", body)
    assert result == "glm-5.3"

def test_route_image_to_vision_already_vision_model():
    body = make_body(with_image=True)
    result = route_image_to_vision(GLM_VISION_MODEL, body)
    assert result == GLM_VISION_MODEL

def test_route_image_to_vision_glm_47():
    body = make_body(with_image=True)
    result = route_image_to_vision("glm-4.7", body)
    assert result == GLM_VISION_MODEL

def test_ctx_model_correction_on_image():
    ctx_model = "glm-5.3"
    body = make_body(with_image=True)
    _early_provider = "glm"
    if _early_provider == "glm":
        if body_has_image(body):
            corrected = route_image_to_vision(ctx_model, body)
            if corrected != ctx_model:
                ctx_model = corrected
    assert ctx_model == GLM_VISION_MODEL

def test_ctx_model_no_correction_without_image():
    ctx_model = "glm-5.3"
    body = make_body(with_image=False)
    _early_provider = "glm"
    if _early_provider == "glm":
        if body_has_image(body):
            corrected = route_image_to_vision(ctx_model, body)
            if corrected != ctx_model:
                ctx_model = corrected
    assert ctx_model == "glm-5.3"

def test_ctx_model_no_correction_non_glm_provider():
    ctx_model = "MiniMax-M2.7"
    body = make_body(with_image=True)
    _early_provider = "minimax"
    if _early_provider == "glm":
        if body_has_image(body):
            corrected = route_image_to_vision(ctx_model, body)
            if corrected != ctx_model:
                ctx_model = corrected
    assert ctx_model == "MiniMax-M2.7"
