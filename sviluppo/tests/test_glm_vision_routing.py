"""Le richieste con immagini non devono finire su un modello text-only.

GLM-5.3 dichiara "currently supports text-only inputs" (doc guides/llm/glm-5.3) e
glm-4.7 non vede le immagini, ma nessuno dei due rifiuta: verificato il 2026-08-18,
un blocco `image` verso glm-5.3 torna HTTP 200 e il modello risponde di non vedere
nulla. Lo stesso body verso glm-4.6V torna 200 e descrive l'immagine. Il router
dirotta, quindi, invece di far rispondere alla cieca il modello di ruolo.
"""
import json
import sys

sys.path.insert(0, 'src')
import glm_backend as glm


def _body(*blocchi):
    return json.dumps({"model": "glm-5.3", "max_tokens": 100,
                       "messages": [{"role": "user", "content": list(blocchi)}]}).encode()


TESTO = {"type": "text", "text": "ciao"}
IMMAGINE = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBOR"}}


def test_solo_testo_non_dirotta():
    for corpo in (_body(TESTO), b'{"messages":[{"role":"user","content":"stringa semplice"}]}'):
        assert glm.route_image_to_vision("glm-5.3", corpo) == "glm-5.3"
        assert glm.route_image_to_vision("glm-4.7", corpo) == "glm-4.7"


def test_immagine_dirotta_sul_modello_di_visione():
    corpo = _body(IMMAGINE, TESTO)
    assert glm.body_has_image(corpo) is True
    assert glm.route_image_to_vision("glm-5.3", corpo) == glm.GLM_VISION_MODEL
    assert glm.route_image_to_vision("glm-4.7", corpo) == glm.GLM_VISION_MODEL


def test_modello_che_gia_vede_resta_dov_e():
    """Se il modello di ruolo e' gia' di visione, non lo si sostituisce."""
    corpo = _body(IMMAGINE)
    for m in ("glm-4.6V", "glm-5V-Turbo"):
        assert glm.route_image_to_vision(m, corpo) == m


def test_body_illeggibile_o_senza_messaggi():
    for corpo in (b'non-json', b'{}', b'{"messages": null}', b'{"messages": [{}]}'):
        assert glm.body_has_image(corpo) is False
        assert glm.route_image_to_vision("glm-5.3", corpo) == "glm-5.3"


def test_il_dirottamento_sopravvive_al_peak_cap():
    """Il cap peak esenta la visione: alle 15 di un mercoledi' il modello resta
    quello che vede, invece di essere declassato a glm-4.7 (text-only)."""
    modello = glm.route_image_to_vision("glm-5.3", _body(IMMAGINE))
    finale, capped = glm.apply_peak_cap(modello)
    assert (finale, capped) == (glm.GLM_VISION_MODEL, False)


if __name__ == "__main__":  # pragma: no cover
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
