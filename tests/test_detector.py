import unittest

from switch_hdmi.detector import PS5State, SimulatedPS5Detector


class SimulatedDetectorTests(unittest.TestCase):
    def test_all_states_and_end_of_sequence(self):
        detector = SimulatedPS5Detector((PS5State.OFF, PS5State.ON, PS5State.REST))
        self.assertEqual(
            [detector.read_state() for _ in range(5)],
            [PS5State.OFF, PS5State.ON, PS5State.REST, PS5State.REST, PS5State.REST],
        )

    def test_repeating_sequence_wraps_to_first_state(self):
        detector = SimulatedPS5Detector((PS5State.ON, PS5State.REST, PS5State.OFF), repeat=True)
        self.assertEqual(
            [detector.read_state() for _ in range(7)],
            [PS5State.ON, PS5State.REST, PS5State.OFF] * 2 + [PS5State.ON],
        )

    def test_single_state_remains_stable_in_both_modes(self):
        for repeat in (False, True):
            with self.subTest(repeat=repeat):
                detector = SimulatedPS5Detector((PS5State.REST,), repeat=repeat)
                self.assertEqual([detector.read_state() for _ in range(3)], [PS5State.REST] * 3)

    def test_detectors_have_independent_positions(self):
        states = (PS5State.ON, PS5State.OFF)
        first, second = SimulatedPS5Detector(states), SimulatedPS5Detector(states)
        first.read_state()
        self.assertEqual(first.read_state(), PS5State.OFF)
        self.assertEqual(second.read_state(), PS5State.ON)

    def test_rejects_empty_or_invalid_sequence(self):
        for states in ((), ("ON",), (None,)):
            with self.subTest(states=states), self.assertRaises(ValueError):
                SimulatedPS5Detector(states)

    def test_rejects_non_boolean_repeat(self):
        with self.assertRaises(ValueError):
            SimulatedPS5Detector((PS5State.ON,), repeat="false")
