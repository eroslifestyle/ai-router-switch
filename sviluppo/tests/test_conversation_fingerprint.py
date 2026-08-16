"""Test suite per conversation_fingerprint con stabilità a system variabile."""
import sys
from pathlib import Path

# Aggiungi src/ al path per importare il modulo.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from router_mode import conversation_fingerprint


class TestConversationFingerprint:
    """Verifica che il fingerprint sia stabile e discriminativo."""

    def test_stability_system_change(self):
        """Stesso primo messaggio + system DIVERSO → stesso fingerprint.
        Questo è il caso che oggi fallisce (regola 2026-08-16)."""
        data1 = {
            "system": "Sistema A molto lungo e specifico.",
            "messages": [
                {"role": "user", "content": "Ciao, come sta il router oggi?"}
            ]
        }
        data2 = {
            "system": "Sistema B completamente diverso, aggiornato con CLAUDE.md nuovo.",
            "messages": [
                {"role": "user", "content": "Ciao, come sta il router oggi?"}
            ]
        }
        fp1 = conversation_fingerprint(data1)
        fp2 = conversation_fingerprint(data2)
        assert fp1 == fp2, f"Stesso messaggio, system diverso deve dare stesso fingerprint: {fp1} != {fp2}"

    def test_stability_advancing_conversation(self):
        """Conversazione che avanza di più turni → fingerprint invariato.
        Il fingerprint dipende SOLO dal primo messaggio utente."""
        data1 = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": "Primo messaggio lungo della conversazione utente."}
            ]
        }
        data2 = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": "Primo messaggio lungo della conversazione utente."},
                {"role": "assistant", "content": "Risposta lunga."},
                {"role": "user", "content": "Secondo messaggio utente."},
                {"role": "assistant", "content": "Altra risposta."},
            ]
        }
        fp1 = conversation_fingerprint(data1)
        fp2 = conversation_fingerprint(data2)
        assert fp1 == fp2, f"Stesso primo messaggio, anche se la chat avanza: {fp1} != {fp2}"

    def test_stability_system_reminder_in_message(self):
        """Stesso primo messaggio, ma con blocco <system-reminder> diverso al suo interno
        → stesso fingerprint. Il blocco viene rimosso prima di hashare."""
        first_msg_base = "Ciao, voglio analizzare il codice del router."
        data1 = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": first_msg_base + "\n<system-reminder>V1 old info</system-reminder>"}
            ]
        }
        data2 = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": first_msg_base + "\n<system-reminder>V2 new info entirely different content here</system-reminder>"}
            ]
        }
        fp1 = conversation_fingerprint(data1)
        fp2 = conversation_fingerprint(data2)
        assert fp1 == fp2, f"Blocco <system-reminder> diverso deve essere ignorato: {fp1} != {fp2}"

    def test_discrimination_different_messages(self):
        """Primi messaggi utente diversi (entrambi lunghi) → fingerprint diversi."""
        data1 = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": "Primo messaggio completamente diverso sul routing mixam e modalità."}
            ]
        }
        data2 = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": "Secondo messaggio completamente diverso sugli upgrade della gerarchia."}
            ]
        }
        fp1 = conversation_fingerprint(data1)
        fp2 = conversation_fingerprint(data2)
        assert fp1 != fp2, f"Messaggi diversi devono dare fingerprint diversi: {fp1} == {fp2}"

    def test_default_no_messages(self):
        """Nessun messaggio utente → 'default'."""
        data = {
            "system": "Sistema",
            "messages": []
        }
        fp = conversation_fingerprint(data)
        assert fp == "default", f"Nessun messaggio deve restituire 'default': {fp}"

    def test_default_empty_message(self):
        """Primo messaggio utente vuoto → 'default'."""
        data = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": ""}
            ]
        }
        fp = conversation_fingerprint(data)
        assert fp == "default", f"Messaggio vuoto deve restituire 'default': {fp}"

    def test_default_message_too_short(self):
        """Primo messaggio utente troppo corto (< 32 char) → 'default'."""
        data = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": "ciao"}
            ]
        }
        fp = conversation_fingerprint(data)
        assert fp == "default", f"Messaggio troppo corto deve restituire 'default': {fp}"

    def test_default_only_system_reminder(self):
        """Messaggio fatto solo di un blocco <system-reminder> → 'default'."""
        data = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": "<system-reminder>solo questo</system-reminder>"}
            ]
        }
        fp = conversation_fingerprint(data)
        assert fp == "default", f"Messaggio solo <system-reminder> deve restituire 'default': {fp}"

    def test_no_exception_malformed_data_empty_dict(self):
        """Input malformato (data vuoto) → nessuna eccezione."""
        data = {}
        try:
            fp = conversation_fingerprint(data)
            assert fp == "default", f"Data vuoto deve restituire 'default': {fp}"
        except Exception as e:
            assert False, f"Non deve sollevare eccezione su data vuoto: {e}"

    def test_no_exception_messages_not_list(self):
        """Input malformato (messages non è lista) → nessuna eccezione."""
        data = {
            "system": "Sistema",
            "messages": "non una lista"
        }
        try:
            fp = conversation_fingerprint(data)
            assert fp == "default", f"Messages non-lista deve restituire 'default': {fp}"
        except Exception as e:
            assert False, f"Non deve sollevare eccezione su messages non-lista: {e}"

    def test_no_exception_content_unexpected_type(self):
        """Input malformato (content di tipo inatteso) → nessuna eccezione."""
        data = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": 12345}  # numero, non stringa
            ]
        }
        try:
            fp = conversation_fingerprint(data)
            # Il comportamento è: 12345 non è stringa né lista, entra in str() e diventa "12345" (5 char) → 'default'
            assert fp == "default", f"Content tipo int deve restituire 'default': {fp}"
        except Exception as e:
            assert False, f"Non deve sollevare eccezione su content tipo int: {e}"

    def test_format_12_hex_chars(self):
        """Forma del risultato: 12 caratteri esadecimali quando identificabile."""
        data = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": "Questo è un primo messaggio che è sufficientemente lungo da non collassare."}
            ]
        }
        fp = conversation_fingerprint(data)
        # Deve essere 12 caratteri hex oppure 'default'
        if fp != "default":
            assert len(fp) == 12, f"Fingerprint identificabile deve essere 12 char, got {len(fp)}: {fp}"
            try:
                int(fp, 16)  # Verifica che sia esadecimale
            except ValueError:
                assert False, f"Fingerprint deve essere esadecimale: {fp}"

    def test_content_as_list_of_blocks(self):
        """Content come lista di blocchi (e.g., con immagini) → estrai 'text'."""
        data = {
            "system": "Sistema",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Primo messaggio con struttura di blocchi sufficientemente lungo."},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
                    ]
                }
            ]
        }
        fp = conversation_fingerprint(data)
        assert fp != "default", f"Content come lista di blocchi deve essere identificabile: {fp}"
        assert len(fp) == 12, f"Fingerprint identificabile deve essere 12 char: {fp}"

    def test_system_as_list_ignored(self):
        """System come lista di blocchi (ignorato comunque, mai incluso nell'hash)."""
        data1 = {
            "system": [{"type": "text", "text": "Sistema A"}],
            "messages": [
                {"role": "user", "content": "Primo messaggio lungo e identificabile per test."}
            ]
        }
        data2 = {
            "system": [{"type": "text", "text": "Sistema B completamente diverso"}],
            "messages": [
                {"role": "user", "content": "Primo messaggio lungo e identificabile per test."}
            ]
        }
        fp1 = conversation_fingerprint(data1)
        fp2 = conversation_fingerprint(data2)
        assert fp1 == fp2, f"System come lista, anche se diverso, deve essere ignorato: {fp1} != {fp2}"

    def test_unclosed_system_reminder_tag(self):
        """Tag <system-reminder> non chiuso → tronca da lì in poi."""
        first_msg = "Messaggio importante prima del tag. <system-reminder>Questo non deve contare e il testo continua oltre il limite senza chiusura e ancora testo qui."
        data = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": first_msg}
            ]
        }
        # Dopo la rimozione, rimane "Messaggio importante prima del tag. " (~36 char) → identificabile
        fp = conversation_fingerprint(data)
        assert fp != "default", f"Messaggio con tag non chiuso deve rimanere identificabile: {fp}"
        assert len(fp) == 12, f"Fingerprint identificabile deve essere 12 char: {fp}"

    def test_multiple_system_reminder_blocks(self):
        """Multipli blocchi <system-reminder> → tutti rimossi."""
        first_msg = "Inizio. <system-reminder>blocco1</system-reminder> Mezzo. <system-reminder>blocco2</system-reminder> Fine."
        data = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": first_msg}
            ]
        }
        fp = conversation_fingerprint(data)
        # Dopo rimozione: "Inizio. Mezzo. Fine." (~20 char) → ancora corto
        # Dipende dalla lunghezza totale. Aggiungiamo testo per rendere > 32 char.
        first_msg = "Inizio molto lungo. <system-reminder>blocco1</system-reminder> Mezzo lungo. <system-reminder>blocco2</system-reminder> Fine lungo."
        data = {
            "system": "Sistema",
            "messages": [
                {"role": "user", "content": first_msg}
            ]
        }
        fp = conversation_fingerprint(data)
        assert fp != "default", f"Multipli blocchi rimossi, testo rimanente identificabile: {fp}"
