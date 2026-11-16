"""The coordinator's text checked by code against the options (agents/claims.py): the colours it gives to their
parts, and the durations and times it cites. The sentences are the ones a model wrote in real reports. No network."""
import pytest

from trip_planner.agents.claims import check_claims, durations

def option(flight_price: str = "A", to: tuple = ("A", "operator"), from_: tuple = ("A", "operator"), minutes: int = 476) -> dict:
    """An option whose parts have these grades: the flight price ("A" confirmed by the airline, "B" a reseller's),
    and each ground leg's (times grade, kind of price)."""
    return {"booking": {"verified": flight_price == "A"}, "to": {"grade": to[0], "price_kind": to[1]},
            "from": {"grade": from_[0], "price_kind": from_[1]}, "total_minutes": minutes}

@pytest.fixture(autouse=True)
def portuguese(settings):
    settings.language = "pt-PT"

def notes(text: str) -> list[str]:
    return [line[2:] for line in text.split("\n") if line.startswith("> ")]

# Coimbra to Antwerp, 2026-10-26, option 5
COIMBRA = """Recomendo a **Opção 5** por ser a mais eficiente em termos de tempo total de viagem.

Quanto à fiabilidade:
- Voo: 🟢 o preço é de revendedor e deve ser verificado no site da Transavia.
- Transporte terrestre (até ao aeroporto): 🔵 confirmado para o dia da viagem.
- Transporte terrestre (do aeroporto ao destino): 🟢 o horário é típico e deve ser reconfirmado para a data da viagem; 🟠 o preço é uma estimativa e deve ser verificado.

A principal vantagem desta opção é a duração total de 7h56, o que a torna a escolha mais rápida face às restantes alternativas apresentadas."""

def coimbra(**grades) -> list[dict]:
    chosen = option(**{"flight_price": "B", "to": ("A", "operator"), "from_": ("B", "estimate"), **grades})
    return [option(minutes=600)] * 4 + [chosen]

def test_matching_colours_kept():
    checked = check_claims(COIMBRA, "Duração total: 7h56", coimbra(), 5)
    assert checked == COIMBRA

def test_wrong_part_colour_corrected():
    checked = check_claims(COIMBRA, "7h56", coimbra(from_=("A", "estimate")), 5)   # the times from the airport were confirmed
    assert "🔵 o horário é típico" in checked and "🟠 o preço é uma estimativa" in checked
    assert len(notes(checked)) == 1 and "Do aeroporto ao destino, horário" in notes(checked)[0] and "corrigido" in notes(checked)[0]

def test_false_confirmed_colour_flagged():
    checked = check_claims(COIMBRA, "7h56", coimbra(to=("B", "operator_typical")), 5)
    assert "(até ao aeroporto): 🔵" in checked   # two parts, two grades: not rewritten, pointed out
    assert any("🔵" in note and "Até ao aeroporto, horário 🟢" in note for note in notes(checked))

def test_unknown_duration_flagged():
    checked = check_claims(COIMBRA, "Duração total: 8h10", coimbra(), 5)
    assert any("7h56" in note for note in notes(checked))

# Coimbra to Paris, 2026-10-22, option 1: a claim about every part, and one about none
PARIS = ("Em termos de fiabilidade, todas as componentes da Opção 1 estão classificadas com 🔵, com horários e tarifas confirmados "
         "diretamente junto dos operadores para a data da viagem, não existindo partes com 🟢 ou 🟠 que precisem de validação prévia.")

def test_all_confirmed_true_believed():
    assert check_claims(PARIS, "", [option(), option()], 1) == PARIS

def test_all_confirmed_false_flagged():
    checked = check_claims(PARIS, "", [option(from_=("A", "tariff")), option()], 1)
    said = notes(checked)
    assert any("todas as partes" in note and "Do aeroporto ao destino, preço 🟢" in note for note in said)
    assert any("nenhuma parte" in note and "🟢" in note for note in said)   # "no part is 🟢" is false too
    assert not any("🟠" in note.split("são:")[0] for note in said)          # no part is 🟠: that one was true

# Antwerp to Coimbra, 2026-10-26: another option, named by number
ANTWERP = ("A principal vantagem desta opção é o preço; a alternativa mais próxima em termos de custo (opção 2) é 61 EUR mais cara e "
           "possui partes com fiabilidade 🟠 que exigiriam verificação adicional.")

def test_other_option_colour_checked():
    assert check_claims(ANTWERP, "61 EUR", [option(), option(to=("B", "estimate"))], 1) == ANTWERP
    assert notes(check_claims(ANTWERP, "61 EUR", [option(to=("C", "model")), option()], 1))   # option 2 has nothing 🟠

# Coimbra to Sciacca, 2026-10-22: a label per line, and "the other options"
SCIACCA = """A fiabilidade desta opção é:
* Voo: 🟢
* Transporte terrestre: 🟢

As restantes opções, apesar de serem alternativas, apresentam uma fiabilidade menor (🟠) por dependerem de modelos para o trajeto terrestre entre o aeroporto e o destino."""

def test_label_names_the_part():
    shown = [option(flight_price="B", to=("B", "tariff"), from_=("B", "tariff")), option(from_=("C", "model")), option(from_=("C", "model"))]
    assert check_claims(SCIACCA, "", shown, 1) == SCIACCA
    shown[1] = shown[2] = option()
    assert len(notes(check_claims(SCIACCA, "", shown, 1))) == 1   # the others have nothing 🟠

# Antwerp to Coimbra, 2026-10-24: times and prices of the ground legs in one sentence
GROUND = ("*   **Transporte terrestre:** 🟢 baseia-se em horários típicos para o dia da semana, mas os preços são 🟠 não verificados "
          "e devem ser confirmados antes da reserva.")

def test_nearby_word_picks_kind():
    assert check_claims(GROUND, "", [option(to=("B", "estimate"), from_=("B", "model"))], 1) == GROUND
    checked = check_claims(GROUND, "", [option(to=("B", "operator"), from_=("B", "operator"))], 1)
    assert notes(checked) and "preço" in notes(checked)[0]   # the prices were read from the operators

def test_no_option_no_judgement():
    """No itinerary at all: the text explains why, and its colours cannot be about any option."""
    assert check_claims(SCIACCA, "", [], 0) == SCIACCA

# ---------------------------------------------------------------- durations and times
@pytest.mark.parametrize("written, minutes", [("7h56", 476), ("2 horas e 5 minutos", 125), ("51 minutos", 51), ("3h", 180), ("90 min", 90)])
def test_durations_read_any_format(written, minutes):
    assert [found[1] for found in durations(f"demora {written} ao todo")] == [minutes]

def test_clock_time_not_duration():
    assert durations("parte às 02:30 e chega às 14:15") == []

def test_differences_and_rounding_believed():
    shown = [option(minutes=476), option(minutes=527)]
    text = "A opção 1 é mais rápida: demora 51 minutos menos, cerca de 8 horas no total."
    assert check_claims(text, "Duração: 7h56", shown, 1) == text

def test_unknown_clock_time_flagged():
    text = "Parte de Coimbra às 2:30 e chega às 14:20."
    checked = check_claims(text, "Saída 02:30, chegada 14:15", [option()], 1)
    assert len(notes(checked)) == 1 and "14:20" in notes(checked)[0] and "2:30" not in notes(checked)[0]

def test_unknown_price_flagged():
    checked = check_claims("A opção 1 custa 52 € no total.", "Total: 45.40 EUR", [option()], 1)
    assert notes(checked) == ["Aviso de integridade: o texto acima cita o preço 52 €, que não aparece no relatório. Confia no relatório."]

def test_rounded_price_believed():
    shown = [{**option(), "total_cost": 45.40}, {**option(), "total_cost": 57.90}]
    text = "A opção 1 custa cerca de 45 €, 12.50 EUR menos que a opção 2 (57.90 EUR)."
    assert notes(check_claims(text, "45.40 EUR | 57.90 EUR", shown, 1)) == []
