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

def test_colours_that_match_the_report_are_left_alone():
    checked = check_claims(COIMBRA, "Duração total: 7h56", coimbra(), 5)
    assert checked == COIMBRA

def test_a_colour_said_of_one_part_that_has_another_is_corrected():
    checked = check_claims(COIMBRA, "7h56", coimbra(from_=("A", "estimate")), 5)   # the times from the airport were confirmed
    assert "🔵 o horário é típico" in checked and "🟠 o preço é uma estimativa" in checked
    assert len(notes(checked)) == 1 and "Do aeroporto ao destino, horário" in notes(checked)[0] and "corrigido" in notes(checked)[0]

def test_a_confirmed_colour_given_to_a_leg_that_is_not_is_pointed_out():
    checked = check_claims(COIMBRA, "7h56", coimbra(to=("B", "operator_typical")), 5)
    assert "(até ao aeroporto): 🔵" in checked   # two parts, two grades: not rewritten, pointed out
    assert any("🔵" in note and "Até ao aeroporto, horário 🟢" in note for note in notes(checked))

def test_a_duration_the_model_was_not_given_is_pointed_out():
    checked = check_claims(COIMBRA, "Duração total: 8h10", coimbra(), 5)
    assert any("7h56" in note for note in notes(checked))

# Coimbra to Paris, 2026-10-22, option 1: a claim about every part, and one about none
PARIS = ("Em termos de fiabilidade, todas as componentes da Opção 1 estão classificadas com 🔵, com horários e tarifas confirmados "
         "diretamente junto dos operadores para a data da viagem, não existindo partes com 🟢 ou 🟠 que precisem de validação prévia.")

def test_every_part_confirmed_is_believed_when_it_is_true():
    assert check_claims(PARIS, "", [option(), option()], 1) == PARIS

def test_every_part_confirmed_is_pointed_out_when_one_is_not():
    checked = check_claims(PARIS, "", [option(from_=("A", "tariff")), option()], 1)
    said = notes(checked)
    assert any("todas as partes" in note and "Do aeroporto ao destino, preço 🟢" in note for note in said)
    assert any("nenhuma parte" in note and "🟢" in note for note in said)   # "no part is 🟢" is false too
    assert not any("🟠" in note.split("são:")[0] for note in said)          # no part is 🟠: that one was true

# Antwerp to Coimbra, 2026-10-26: another option, named by number
ANTWERP = ("A principal vantagem desta opção é o preço; a alternativa mais próxima em termos de custo (opção 2) é 61 EUR mais cara e "
           "possui partes com fiabilidade 🟠 que exigiriam verificação adicional.")

def test_a_colour_said_of_another_option_is_checked_against_that_option():
    assert check_claims(ANTWERP, "", [option(), option(to=("B", "estimate"))], 1) == ANTWERP
    assert notes(check_claims(ANTWERP, "", [option(to=("C", "model")), option()], 1))   # option 2 has nothing 🟠

# Coimbra to Sciacca, 2026-10-22: a label per line, and "the other options"
SCIACCA = """A fiabilidade desta opção é:
* Voo: 🟢
* Transporte terrestre: 🟢

As restantes opções, apesar de serem alternativas, apresentam uma fiabilidade menor (🟠) por dependerem de modelos para o trajeto terrestre entre o aeroporto e o destino."""

def test_a_label_says_which_part_and_the_others_are_the_other_options():
    shown = [option(flight_price="B", to=("B", "tariff"), from_=("B", "tariff")), option(from_=("C", "model")), option(from_=("C", "model"))]
    assert check_claims(SCIACCA, "", shown, 1) == SCIACCA
    shown[1] = shown[2] = option()
    assert len(notes(check_claims(SCIACCA, "", shown, 1))) == 1   # the others have nothing 🟠

# Antwerp to Coimbra, 2026-10-24: times and prices of the ground legs in one sentence
GROUND = ("*   **Transporte terrestre:** 🟢 baseia-se em horários típicos para o dia da semana, mas os preços são 🟠 não verificados "
          "e devem ser confirmados antes da reserva.")

def test_the_word_next_to_a_colour_says_whether_it_is_the_times_or_the_price():
    assert check_claims(GROUND, "", [option(to=("B", "estimate"), from_=("B", "model"))], 1) == GROUND
    checked = check_claims(GROUND, "", [option(to=("B", "operator"), from_=("B", "operator"))], 1)
    assert notes(checked) and "preço" in notes(checked)[0]   # the prices were read from the operators

def test_without_an_option_to_speak_of_no_colour_is_judged():
    """No itinerary at all: the text explains why, and its colours cannot be about any option."""
    assert check_claims(SCIACCA, "", [], 0) == SCIACCA

# ---------------------------------------------------------------- durations and times
@pytest.mark.parametrize("written, minutes", [("7h56", 476), ("2 horas e 5 minutos", 125), ("51 minutos", 51), ("3h", 180), ("90 min", 90)])
def test_durations_are_read_however_they_are_written(written, minutes):
    assert [found[1] for found in durations(f"demora {written} ao todo")] == [minutes]

def test_a_clock_time_is_not_a_duration():
    assert durations("parte às 02:30 e chega às 14:15") == []

def test_a_difference_between_two_options_and_a_rounded_duration_are_believed():
    shown = [option(minutes=476), option(minutes=527)]
    text = "A opção 1 é mais rápida: demora 51 minutos menos, cerca de 8 horas no total."
    assert check_claims(text, "Duração: 7h56", shown, 1) == text

def test_a_clock_time_not_in_the_report_is_pointed_out():
    text = "Parte de Coimbra às 2:30 e chega às 14:20."
    checked = check_claims(text, "Saída 02:30, chegada 14:15", [option()], 1)
    assert len(notes(checked)) == 1 and "14:20" in notes(checked)[0] and "2:30" not in notes(checked)[0]
