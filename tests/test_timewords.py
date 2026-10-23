"""Time-bound fact detection, English and Spanish (invented sentences)."""

from __future__ import annotations

import unittest

from steel.timewords import is_time_bound

TIME_BOUND = [
    "Mara is visiting next weekend.",
    "User has a meeting tomorrow",
    "User is cooking tonight",
    "User is busy this week",
    "User ate paella yesterday",
    "User flies out in 3 days",
    "User sees the doctor next Tuesday",
    "Mañana tengo médico",
    "Lucía viaja este fin de semana",
    "El usuario tiene cita esta tarde",
    "Hoy el usuario está cansado",
    "El usuario cenó paella ayer",
    "Mi hermana llega el lunes que viene",
    "El usuario viaja pasado mañana",
    "El usuario sale dentro de dos días",
    "La semana que viene hay examen",
]
DURABLE = [
    "User lives in Rotterdam",
    "User runs every morning",
    "User studies on Mondays",
    "User moves to Madrid next month",  # month scale: deliberately durable
    "User was born in 1990",
    "El usuario trabaja por la mañana",
    "Cada mañana el usuario corre",
    "Hoy en día el usuario usa Linux",
    "Los fines de semana el usuario toca el cello",
    "Lucía vive en Valencia",
]


class TimeWordsTest(unittest.TestCase):
    def test_time_bound_sentences(self) -> None:
        for text in TIME_BOUND:
            self.assertTrue(is_time_bound(text), text)

    def test_durable_sentences_are_not_routed(self) -> None:
        for text in DURABLE:
            self.assertFalse(is_time_bound(text), text)


if __name__ == "__main__":
    unittest.main()
