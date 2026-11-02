"""Delphi Trials v2: about 1,000 more varied questions for the real-model run.

Same four task types and scorers as the v1 Delphi Trials (no code questions), but much
wider: ~110 distinct multiple-choice facts and ~105 distinct short-answer lore questions
across Greek myth, history and philosophy, arithmetic in three difficulty tiers (one, two
and three steps, with remainders, percentages and distractor numbers), and prophecies
with form constraints the judge must check. Ground truth is computed (reckoning) or
hand-written from well-known, single-answer facts (facts, lore).

Built to be disjoint from v1: the near-duplicate filter is seeded with every v1 prompt, so
no v2 question is an exact or near duplicate of anything v1 asked. Deterministic given seed.
Not tuned to any model: the questions were written before any v2 answer was seen.
"""
from __future__ import annotations

import random

from delphi.data.schema import Dataset, Query, TaskType

_NAMES = ["Kleon", "Xanthe", "Theron", "Iola", "Damon", "Phaedra", "Nikias", "Berenike",
          "Lysandra", "Aristo", "Melitta", "Kallias", "Thais", "Eudoros", "Chloris", "Myron"]

# --- FACTS: (question, correct, distractors) --------------------------------------------
_FACTS = [
    ("Who is the king of the Greek gods?", "Zeus", ["Poseidon", "Hades", "Apollo", "Ares"]),
    ("Who is the Greek god of the sea?", "Poseidon", ["Zeus", "Hermes", "Hephaestus", "Ares"]),
    ("Who rules the Greek underworld?", "Hades", ["Ares", "Hermes", "Cronus", "Apollo"]),
    ("Who is the Greek goddess of wisdom?", "Athena", ["Hera", "Artemis", "Demeter", "Hestia"]),
    ("Who is the Greek goddess of love and beauty?", "Aphrodite",
     ["Athena", "Hera", "Persephone", "Nike"]),
    ("Who is the Greek god of wine?", "Dionysus", ["Apollo", "Hermes", "Pan", "Ares"]),
    ("Who is the smith of the Greek gods?", "Hephaestus",
     ["Ares", "Hermes", "Apollo", "Poseidon"]),
    ("Who is the Greek goddess of the hunt?", "Artemis",
     ["Athena", "Hera", "Demeter", "Aphrodite"]),
    ("Who is the Greek god of war?", "Ares", ["Apollo", "Hermes", "Hephaestus", "Dionysus"]),
    ("Who is the Greek goddess of the harvest and grain?", "Demeter",
     ["Hera", "Hestia", "Artemis", "Athena"]),
    ("Who is the queen of the Greek gods?", "Hera",
     ["Athena", "Demeter", "Aphrodite", "Hestia"]),
    ("Who is the Greek goddess of the hearth?", "Hestia",
     ["Hera", "Demeter", "Athena", "Artemis"]),
    ("Who became queen of the underworld as the wife of Hades?", "Persephone",
     ["Demeter", "Hecate", "Hera", "Eurydice"]),
    ("Who is the twin sister of Apollo?", "Artemis",
     ["Athena", "Aphrodite", "Hera", "Persephone"]),
    ("Who is the mother of Apollo and Artemis?", "Leto", ["Hera", "Maia", "Semele", "Metis"]),
    ("Who is the mother of Hermes?", "Maia", ["Leto", "Semele", "Hera", "Alcmene"]),
    ("Who is the mortal mother of Dionysus?", "Semele", ["Leto", "Maia", "Danae", "Io"]),
    ("Who is the mother of Heracles?", "Alcmene", ["Danae", "Leda", "Semele", "Io"]),
    ("Who is the mother of Perseus?", "Danae", ["Alcmene", "Leda", "Io", "Europa"]),
    ("Who is the father of Zeus?", "Cronus", ["Uranus", "Atlas", "Prometheus", "Oceanus"]),
    ("Which Titan stole fire for humanity?", "Prometheus",
     ["Atlas", "Epimetheus", "Cronus", "Hyperion"]),
    ("Which brother of Prometheus married Pandora?", "Epimetheus",
     ["Atlas", "Menoetius", "Hyperion", "Cronus"]),
    ("Who solved the riddle of the Sphinx?", "Oedipus",
     ["Theseus", "Jason", "Perseus", "Cadmus"]),
    ("Who slew the Minotaur?", "Theseus", ["Heracles", "Perseus", "Jason", "Achilles"]),
    ("Who gave Theseus the thread to escape the labyrinth?", "Ariadne",
     ["Medea", "Phaedra", "Helen", "Circe"]),
    ("Who built the labyrinth of Crete?", "Daedalus",
     ["Icarus", "Hephaestus", "Minos", "Talos"]),
    ("Which king had the labyrinth built?", "Minos",
     ["Aegeus", "Priam", "Agamemnon", "Midas"]),
    ("Who led the Argonauts?", "Jason", ["Heracles", "Theseus", "Orpheus", "Peleus"]),
    ("What did the Argonauts sail to find?", "the Golden Fleece",
     ["the Golden Apple", "the Aegis", "the Trident", "the Palladium"]),
    ("Which sorceress helped Jason win his quest?", "Medea",
     ["Circe", "Ariadne", "Calypso", "Hecate"]),
    ("Which Trojan prince carried Helen away to Troy?", "Paris",
     ["Hector", "Aeneas", "Priam", "Troilus"]),
    ("Which Trojan prince was slain by Achilles before the walls of Troy?", "Hector",
     ["Paris", "Aeneas", "Priam", "Sarpedon"]),
    ("Who was king of Troy during the Trojan War?", "Priam",
     ["Hector", "Agamemnon", "Menelaus", "Laomedon"]),
    ("Who commanded the Greek army at Troy?", "Agamemnon",
     ["Menelaus", "Achilles", "Odysseus", "Nestor"]),
    ("Who was Helen's Greek husband?", "Menelaus",
     ["Agamemnon", "Paris", "Odysseus", "Achilles"]),
    ("Who devised the Trojan Horse?", "Odysseus", ["Achilles", "Agamemnon", "Ajax", "Nestor"]),
    ("Which island was the home of Odysseus?", "Ithaca",
     ["Crete", "Rhodes", "Delos", "Lesbos"]),
    ("Who is the son of Odysseus?", "Telemachus",
     ["Neoptolemus", "Orestes", "Astyanax", "Patroclus"]),
    ("Which one-eyed giant did Odysseus blind?", "Polyphemus",
     ["Argus", "Typhon", "Talos", "Antaeus"]),
    ("Which nymph kept Odysseus on her island for seven years?", "Calypso",
     ["Circe", "Nausicaa", "Penelope", "Scylla"]),
    ("Which enchantress turned Odysseus's men into pigs?", "Circe",
     ["Calypso", "Medea", "Hecate", "Scylla"]),
    ("Which Trojan prophetess was cursed never to be believed?", "Cassandra",
     ["Andromache", "Electra", "Hecuba", "Polyxena"]),
    ("Who was the dearest companion of Achilles?", "Patroclus",
     ["Ajax", "Diomedes", "Nestor", "Hector"]),
    ("Which hero performed twelve labours?", "Heracles",
     ["Theseus", "Perseus", "Jason", "Achilles"]),
    ("Which many-headed serpent did Heracles slay?", "the Hydra",
     ["the Chimera", "Typhon", "Ladon", "Scylla"]),
    ("What is the name of the winged horse of Greek myth?", "Pegasus",
     ["Arion", "Bucephalus", "Xanthus", "Chiron"]),
    ("Which hero rode Pegasus against the Chimera?", "Bellerophon",
     ["Perseus", "Theseus", "Jason", "Heracles"]),
    ("Which wise centaur taught Achilles?", "Chiron", ["Nessus", "Pholus", "Eurytion", "Pan"]),
    ("Which youth fell in love with his own reflection?", "Narcissus",
     ["Hyacinthus", "Adonis", "Ganymede", "Endymion"]),
    ("Which nymph could only repeat the words of others?", "Echo",
     ["Daphne", "Syrinx", "Io", "Callisto"]),
    ("Which nymph became a laurel tree to escape Apollo?", "Daphne",
     ["Echo", "Syrinx", "Arethusa", "Io"]),
    ("Which king stood in water he could never drink, beneath fruit he could never reach?",
     "Tantalus", ["Sisyphus", "Ixion", "Midas", "Minos"]),
    ("Which king was doomed to roll a boulder uphill forever?", "Sisyphus",
     ["Tantalus", "Ixion", "Prometheus", "Atlas"]),
    ("Who ferries the souls of the dead across the river?", "Charon",
     ["Cerberus", "Hermes", "Thanatos", "Minos"]),
    ("Who is the Greek personification of death?", "Thanatos",
     ["Hypnos", "Nyx", "Erebus", "Charon"]),
    ("Who is the Greek god of sleep?", "Hypnos", ["Thanatos", "Nyx", "Erebus", "Hemera"]),
    ("Who is the Greek goddess of victory?", "Nike", ["Athena", "Tyche", "Eris", "Iris"]),
    ("Which goddess is the rainbow and a messenger of the gods?", "Iris",
     ["Nike", "Eos", "Hebe", "Selene"]),
    ("Who is the Greek goddess of the dawn?", "Eos", ["Selene", "Nyx", "Iris", "Hebe"]),
    ("Which goddess threw the golden apple of discord?", "Eris",
     ["Nemesis", "Ate", "Nike", "Hera"]),
    ("Who are the nine goddesses of the arts?", "the Muses",
     ["the Fates", "the Graces", "the Furies", "the Hours"]),
    ("Which three goddesses spin, measure and cut the thread of life?", "the Fates",
     ["the Muses", "the Graces", "the Furies", "the Gorgons"]),
    ("Which of the Gorgons was mortal?", "Medusa", ["Stheno", "Euryale", "Echidna", "Lamia"]),
    ("Who is the Greek goddess of retribution?", "Nemesis",
     ["Eris", "Tyche", "Nike", "Hecate"]),
    ("On which mountain did the Olympian gods live?", "Olympus",
     ["Parnassus", "Ida", "Athos", "Etna"]),
    ("Which Muse presides over epic poetry?", "Calliope",
     ["Clio", "Erato", "Urania", "Thalia"]),
    ("Which Muse presides over history?", "Clio",
     ["Calliope", "Urania", "Euterpe", "Thalia"]),
    ("Which Muse presides over astronomy?", "Urania",
     ["Clio", "Calliope", "Terpsichore", "Polyhymnia"]),
    ("Which poet is credited with the Iliad and the Odyssey?", "Homer",
     ["Hesiod", "Pindar", "Sophocles", "Virgil"]),
    ("Which poet wrote the Theogony?", "Hesiod", ["Homer", "Pindar", "Aeschylus", "Sappho"]),
    ("Which philosopher was the teacher of Plato?", "Socrates",
     ["Aristotle", "Pythagoras", "Thales", "Diogenes"]),
    ("Which philosopher tutored Alexander the Great?", "Aristotle",
     ["Plato", "Socrates", "Zeno", "Epicurus"]),
    ("Who wrote The Republic?", "Plato", ["Aristotle", "Socrates", "Herodotus", "Xenophon"]),
    ("Who is called the Father of History?", "Herodotus",
     ["Thucydides", "Xenophon", "Plutarch", "Polybius"]),
    ("Who wrote the History of the Peloponnesian War?", "Thucydides",
     ["Herodotus", "Plutarch", "Arrian", "Polybius"]),
    ("Which Spartan king led the Greeks at Thermopylae?", "Leonidas",
     ["Lysander", "Agesilaus", "Pausanias", "Cleomenes"]),
    ("At which battle in 490 BC did Athens defeat the Persians?", "Marathon",
     ["Salamis", "Plataea", "Thermopylae", "Mycale"]),
    ("Which naval battle of 480 BC was a Greek victory over Persia?", "Salamis",
     ["Marathon", "Actium", "Plataea", "Mycale"]),
    ("Which Athenian statesman led the building of the Parthenon?", "Pericles",
     ["Themistocles", "Solon", "Cleisthenes", "Alcibiades"]),
    ("Which temple on the Athenian Acropolis was dedicated to Athena Parthenos?",
     "the Parthenon", ["the Pantheon", "the Olympieion", "the Hephaisteion"]),
    ("Which Greek god did the Romans call Jupiter?", "Zeus", ["Apollo", "Ares", "Poseidon"]),
    ("Which Greek god did the Romans call Mars?", "Ares", ["Apollo", "Hermes", "Hephaestus"]),
    ("Which Greek goddess did the Romans call Venus?", "Aphrodite",
     ["Athena", "Hera", "Artemis"]),
    ("Which Greek god did the Romans call Mercury?", "Hermes", ["Apollo", "Ares", "Dionysus"]),
    ("Which Greek god did the Romans call Neptune?", "Poseidon", ["Zeus", "Hades", "Ares"]),
    ("Which Greek goddess did the Romans call Minerva?", "Athena",
     ["Hera", "Artemis", "Demeter"]),
    ("Which Greek goddess did the Romans call Diana?", "Artemis",
     ["Athena", "Hera", "Aphrodite"]),
    ("Which Greek god did the Romans call Vulcan?", "Hephaestus",
     ["Hermes", "Ares", "Apollo"]),
    ("Which Greek goddess did the Romans call Ceres?", "Demeter",
     ["Hera", "Hestia", "Persephone"]),
    ("Which Greek god did the Romans call Bacchus?", "Dionysus", ["Apollo", "Pan", "Hermes"]),
    ("Where were the ancient Olympic Games held?", "Olympia",
     ["Delphi", "Athens", "Corinth", "Sparta"]),
    ("Which games were held at the Isthmus of Corinth?", "the Isthmian Games",
     ["the Pythian Games", "the Nemean Games", "the Olympic Games"]),
    ("Which wonder of the ancient world stood at Rhodes?", "the Colossus",
     ["the Lighthouse", "the Mausoleum", "the Hanging Gardens"]),
    ("In which city stood the great Lighthouse, a wonder of the ancient world?",
     "Alexandria", ["Rhodes", "Athens", "Halicarnassus", "Ephesus"]),
    ("In which city stood the Temple of Artemis, a wonder of the ancient world?", "Ephesus",
     ["Delphi", "Athens", "Olympia", "Corinth"]),
    ("Which Greek is famous for the theorem about right triangles?", "Pythagoras",
     ["Euclid", "Archimedes", "Thales", "Eratosthenes"]),
    ("Who is said to have shouted 'Eureka' in his bath?", "Archimedes",
     ["Euclid", "Pythagoras", "Hippocrates", "Aristotle"]),
    ("Who wrote the geometry textbook called the Elements?", "Euclid",
     ["Archimedes", "Pythagoras", "Ptolemy"]),
    ("Which Greek physician is called the Father of Medicine?", "Hippocrates",
     ["Galen", "Aristotle", "Archimedes"]),
    ("Which son of Apollo was the god of medicine?", "Asclepius",
     ["Aristaeus", "Hermes", "Orpheus"]),
    ("Which Athenian general commanded at Marathon?", "Miltiades",
     ["Themistocles", "Pericles", "Leonidas"]),
    ("Which huntress lost a footrace after stopping for golden apples?", "Atalanta",
     ["Artemis", "Penthesilea", "Callisto"]),
    ("Which queen of the Amazons did Achilles slay at Troy?", "Penthesilea",
     ["Hippolyta", "Antiope", "Atalanta"]),
    ("Whose girdle did Heracles fetch as one of his labours?", "Hippolyta",
     ["Penthesilea", "Medusa", "Atalanta"]),
    ("Which god took the form of a bull to carry off Europa?", "Zeus",
     ["Poseidon", "Apollo", "Ares"]),
    ("Who founded the city of Thebes?", "Cadmus", ["Theseus", "Perseus", "Minos"]),
    ("Which king was given donkey's ears by Apollo?", "Midas", ["Minos", "Croesus", "Priam"]),
    ("Who killed the Calydonian boar?", "Meleager", ["Theseus", "Jason", "Heracles"]),
]
_MC_INTROS = ["Answer with the single letter.", "Reply with one letter only.",
              "Choose one option and answer with its letter."]

# --- LORE: (question, short reference answer) --------------------------------------------
_LORE = [
    ("What weapon does Zeus hurl?", "the thunderbolt"),
    ("What three-pronged weapon does Poseidon carry?", "the trident"),
    ("Which bird is sacred to Athena?", "the owl"),
    ("Which tree did Athena give Athens to win the city?", "the olive tree"),
    ("What is the food of the Greek gods called?", "ambrosia"),
    ("What is the drink of the Greek gods called?", "nectar"),
    ("What flows in the veins of the Greek gods instead of blood?", "ichor"),
    ("Which stringed instrument did Apollo play?", "the lyre"),
    ("Into what did Medusa's gaze turn those who looked at her?", "stone"),
    ("Which part of Achilles's body was his only weak spot?", "his heel"),
    ("What was the name of Odysseus's faithful wife?", "Penelope"),
    ("What was the name of Odysseus's old dog?", "Argos"),
    ("Which creatures lured sailors to their deaths with song?", "the Sirens"),
    ("What did Odysseus put in his crew's ears to block the Sirens' song?", "wax"),
    ("Which fruit did Persephone eat in the underworld?", "the pomegranate"),
    ("Which sea is named after Theseus's father?", "the Aegean Sea"),
    ("What colour sail was Theseus meant to raise if he returned alive?", "white"),
    ("What did Ariadne give Theseus to find his way out of the labyrinth?", "a ball of thread"),
    ("What did Daedalus use to make wings for himself and Icarus?", "feathers and wax"),
    ("Which riddling creature with a lion's body guarded the road to Thebes?", "the Sphinx"),
    ("What is the answer to the riddle of the Sphinx?", "man"),
    ("What did Prometheus steal from the gods?", "fire"),
    ("Which bird ate Prometheus's liver each day?", "an eagle"),
    ("In Hesiod's ages of man, which age came first?", "the Golden Age"),
    ("Which beast did Heracles kill in his first labour?", "the Nemean lion"),
    ("Whose stables did Heracles clean in a single day?", "King Augeas"),
    ("What did Heracles fetch from the garden of the Hesperides?", "golden apples"),
    ("What kind of creature was Chiron?", "a centaur"),
    ("What creature was half man and half bull?", "the Minotaur"),
    ("On which island was the Minotaur kept?", "Crete"),
    ("What is the name of the goatskin shield of Zeus and Athena?", "the aegis"),
    ("Which goddess's sacred bird is the peacock?", "Hera"),
    ("Which river of the underworld brings forgetfulness?", "the Lethe"),
    ("What did Perseus use to look at Medusa safely?", "a polished shield"),
    ("Which winged horse sprang from Medusa's neck?", "Pegasus"),
    ("What was the name of the Argonauts' ship?", "the Argo"),
    ("In which land was the Golden Fleece kept?", "Colchis"),
    ("What did Jason lose in a river, as a prophecy foretold?", "a sandal"),
    ("What did the Greeks hide inside to enter Troy?", "a wooden horse"),
    ("Which sea nymph was the mother of Achilles?", "Thetis"),
    ("Who was the husband of Aphrodite?", "Hephaestus"),
    ("Who was the Greek god of the west wind?", "Zephyrus"),
    ("Which flower is named after the youth who loved his reflection?", "the narcissus"),
    ("What did victors at the ancient Olympic Games receive as a prize?", "an olive wreath"),
    ("What did victors at the Pythian Games receive as a prize?", "a laurel wreath"),
    ("Which Greek city-state was famous for its harsh military training?", "Sparta"),
    ("What is the rocky citadel of Athens called?", "the Acropolis"),
    ("What was the open marketplace of a Greek city called?", "the agora"),
    ("What was the Athenian vote to exile a citizen for ten years called?", "ostracism"),
    ("Who was the blind seer of Thebes?", "Tiresias"),
    ("Who killed his father and married his mother without knowing it?", "Oedipus"),
    ("Which daughter of Oedipus buried her brother against the king's order?", "Antigone"),
    ("Which playwright wrote Oedipus Rex?", "Sophocles"),
    ("Which playwright wrote the Oresteia?", "Aeschylus"),
    ("Which playwright wrote the tragedy Medea?", "Euripides"),
    ("Which comic playwright wrote The Clouds?", "Aristophanes"),
    ("Which philosopher was sentenced to die by drinking hemlock?", "Socrates"),
    ("What was the name of the school Plato founded in Athens?", "the Academy"),
    ("What was the name of the school Aristotle founded in Athens?", "the Lyceum"),
    ("What is the Greek word for a city-state?", "polis"),
    ("Which Persian king led the invasion stopped for a time at Thermopylae?", "Xerxes"),
    ("Which Persian king sent the army defeated at Marathon?", "Darius"),
    ("What was the name of Alexander the Great's horse?", "Bucephalus"),
    ("Which city did Alexander the Great found in Egypt?", "Alexandria"),
    ("Who was the father of Alexander the Great?", "Philip of Macedon"),
    ("What was the sacred spring at Delphi called?", "the Castalian Spring"),
    ("Which god was said to rule Delphi in winter while Apollo was away?", "Dionysus"),
    ("Who was the Greek goddess of the moon?", "Selene"),
    ("Which Titan drove the chariot of the sun across the sky?", "Helios"),
    ("Who was turned into a spider after a weaving contest with Athena?", "Arachne"),
    ("Which Trojan prince judged the contest of beauty between three goddesses?", "Paris"),
    ("What words were written on the golden apple of discord?", "to the fairest"),
    ("Which god fell in love with Psyche?", "Eros"),
    ("Which giant did Heracles defeat by lifting him off the earth?", "Antaeus"),
    ("Which nymph was turned into reeds while fleeing Pan?", "Syrinx"),
    ("What bird did Zeus become to approach Leda?", "a swan"),
    ("What power did the helmet of Hades give its wearer?", "invisibility"),
    ("What did the seven daughters of Atlas, the Pleiades, become?", "stars"),
    ("Which goddess was born from the foam of the sea?", "Aphrodite"),
    ("Which of the three Fates cuts the thread of life?", "Atropos"),
    ("Which aged king of Pylos gave counsel to the Greeks at Troy?", "Nestor"),
    ("What was the name of Hector's wife?", "Andromache"),
    ("What was the name of Agamemnon's wife, who killed him?", "Clytemnestra"),
    ("Which son of Agamemnon avenged his father's murder?", "Orestes"),
    ("Which goddess turned the hunter Actaeon into a stag?", "Artemis"),
    ("Whom did Orpheus lose by looking back as they left the underworld?", "Eurydice"),
    ("Which hero of Troy carried a huge shield and was the son of Telamon?", "Ajax"),
    ("What did the ancient Greeks call the Black Sea?", "the Euxine"),
    ("Which language did the Linear B script record?", "Greek"),
    ("What kind of figure is Orion in Greek myth?", "a hunter"),
    ("Which god carries a staff called the caduceus?", "Hermes"),
    ("What did King Midas wish everything he touched would turn into?", "gold"),
    ("Which island was the birthplace of Apollo and Artemis?", "Delos"),
    ("Which monster guarded the strait opposite the whirlpool Charybdis?", "Scylla"),
    ("Which goddess of witchcraft is linked with crossroads?", "Hecate"),
    ("What is the name of the herb Hermes gave Odysseus against Circe's magic?", "moly"),
    ("Which hero killed the Chimera?", "Bellerophon"),
    ("Who was the first king of Athens in some myths, half man and half serpent?", "Cecrops"),
    ("Who was the Greek god of the wild, with the legs of a goat?", "Pan"),
    ("Which Titaness was the mother of the Muses?", "Mnemosyne"),
    ("What was the name of the Spartan queen whose abduction began the Trojan War?", "Helen"),
    ("Which river did Heracles divert to clean the stables?", "the Alpheus"),
    ("What was the name of Jason's wife before he left her for Glauce?", "Medea"),
    ("Which hero's son was Telegonus, born to Circe?", "Odysseus"),
]
_LORE_FRAMES = ["{name} asks, and you answer in a few words: {q}",
                "Briefly, please: {q}",
                "A pilgrim at Delphi wants a short answer. {q}"]

# --- PROPHECY --------------------------------------------------------------------------
_PETITIONERS = ["a weary shepherd", "a Spartan general", "an Athenian merchant",
                "a lovesick poet", "an exiled prince", "a rival oracle", "a curious child",
                "a doubting philosopher", "a widowed queen", "a ship's captain",
                "a temple slave seeking freedom", "an ageing athlete", "a young potter",
                "a Corinthian banker", "a runaway bride", "a Theban farmer"]
_TOPICS = ["a coming war", "a long voyage", "an uncertain marriage", "a failing harvest",
           "a treacherous ally", "a hidden inheritance", "a rival's ambition",
           "a cursed bloodline", "a lost brother", "an omen of eagles", "a broken oath",
           "a famine to come", "a new colony overseas", "a contested election",
           "a sick child", "a debt that cannot be paid", "an apprenticeship", "a duel at dawn",
           "a flood on the river", "a stolen statue", "the founding of a city",
           "a secret love", "an earthquake", "a plague in the harbour", "a journey home",
           "a son's first battle", "a fortune found in the sea", "a quarrel between brothers"]
_FORMS = ["", " Give it in exactly three lines.", " Make it a single rhyming couplet.",
          " Use no more than twenty-five words.", " Begin it with the word 'When'.",
          " Mention a raven somewhere in it.", " Phrase it as a riddle."]


def _facts(rng: random.Random) -> Query:
    q, correct, distractors = _FACTS[rng.randrange(len(_FACTS))]
    picks = [correct] + rng.sample(distractors, 3)
    rng.shuffle(picks)
    letters = "ABCD"
    body = "\n".join(f"{L}) {v}" for L, v in zip(letters, picks))
    prompt = f"{_MC_INTROS[rng.randrange(len(_MC_INTROS))]}\n{q}\n{body}"
    return Query(Query.make_id(prompt), prompt, TaskType.KNOWLEDGE_MC, domain="facts",
                 answer=letters[picks.index(correct)], metadata={"base": q})


def _lore(rng: random.Random) -> Query:
    q, ans = _LORE[rng.randrange(len(_LORE))]
    frame = _LORE_FRAMES[rng.randrange(len(_LORE_FRAMES))]
    prompt = frame.format(name=_NAMES[rng.randrange(len(_NAMES))], q=q)
    return Query(Query.make_id(prompt), prompt, TaskType.OPEN_QA, domain="lore", answer=ans,
                 metadata={"base": q})


def _prophecy(rng: random.Random) -> Query:
    name = _NAMES[rng.randrange(len(_NAMES))]
    who = _PETITIONERS[rng.randrange(len(_PETITIONERS))]
    topic = _TOPICS[rng.randrange(len(_TOPICS))]
    form = _FORMS[rng.randrange(len(_FORMS))]
    prompt = (f"{name}, {who}, comes to the Oracle seeking a prophecy about {topic}. "
              f"Deliver a cryptic yet wise prophecy.{form}")
    return Query(Query.make_id(prompt), prompt, TaskType.OPEN_ENDED, domain="prophecy",
                 metadata={"form": form.strip()})


def _reckoning(rng: random.Random) -> Query:
    """Three tiers: one step, two steps, three steps or a twist (remainder, percent,
    a distractor number). Every answer is a non-negative integer."""
    r = rng.randint
    tier = rng.randrange(3)
    kind = rng.randrange(4)
    name = _NAMES[rng.randrange(len(_NAMES))]
    if tier == 0:
        a, b = r(12, 99), r(3, 60)
        prompt, ans = [
            (f"{name} has {a} olives and eats {min(a - 1, b)}. How many are left?",
             a - min(a - 1, b)),
            (f"A temple has {a} columns on one side and {b} on the other. How many columns?",
             a + b),
            (f"Each of {b} ships carries {a} rowers. How many rowers in all?", a * b),
            (f"{a * b} figs are shared equally among {b} pilgrims. How many does each get?", a),
        ][kind]
    elif tier == 1:
        a, b, c = r(4, 30), r(3, 25), r(2, 40)
        prompt, ans = [
            (f"{name} buys {a} jars at {b} obols each and pays a {c}-obol toll. "
             f"How many obols in total?", a * b + c),
            (f"A phalanx of {a} rows of {b} men loses {min(c, a * b - 1)} men. "
             f"How many remain?", a * b - min(c, a * b - 1)),
            (f"{a} wagons each carry {b} sacks, and {c} more sacks arrive by boat. "
             f"How many sacks?", a * b + c),
            (f"{name} earns {a} drachmae a day for {b} days, then spends {min(c, a * b)}. "
             f"How many drachmae are left?", a * b - min(c, a * b)),
        ][kind]
    else:
        a, b, c = r(5, 40), r(3, 12), r(2, 9)
        if kind == 0:
            n = a * b + (c % b)
            prompt, ans = (f"{n} amphorae are packed {b} to a crate. After filling as many "
                           f"full crates as possible, how many amphorae are left over?", n % b)
        elif kind == 1:
            pct = rng.choice([10, 20, 25, 50])
            base = rng.choice([20, 40, 60, 80, 120, 200, 400]) * (100 // pct) // 5 * 5
            base -= base % (100 // pct)
            prompt, ans = (f"A tax of {pct} percent is taken from {base} measures of grain. "
                           f"How many measures are taken?", base * pct // 100)
        elif kind == 2:
            prompt, ans = (f"{name}, aged {r(20, 70)}, plants {a} rows of {b} vines, then "
                           f"{c} more rows of {b}. How many vines did {name} plant?",
                           (a + c) * b)
        else:
            x, y, z = r(10, 60), r(10, 60), r(10, 60)
            total = x + y + z
            z += (3 - total % 3) % 3
            prompt, ans = (f"Three runners finish in {x}, {y} and {z} heartbeats of the "
                           f"judge's count. What is their average?", (x + y + z) // 3)
    prompt += " Answer with a number."
    return Query(Query.make_id(prompt), prompt, TaskType.MATH, domain="reckoning",
                 answer=str(ans), metadata={"tier": tier})


_GENERATORS = {"prophecy": _prophecy, "facts": _facts, "reckoning": _reckoning, "lore": _lore}


def generate_v2(n_per_domain: int = 250, seed: int = 20261028, exclude: list[str] = (),
                max_per_base: int = 3) -> tuple[Dataset, dict[str, int]]:
    """~4 x n_per_domain questions, disjoint from `exclude` (v1's prompts), with at most
    `max_per_base` phrasings of any one fact or lore question. Returns the dataset and the
    kept count per domain (a shortfall is reported, never hidden)."""
    from delphi.eval.contamination import NearDupFilter

    rng = random.Random(seed)  # noqa: S311 - synthetic data generation, not cryptographic
    ndf = NearDupFilter()
    for p in exclude:
        ndf.accept(p)
    queries: list[Query] = []
    kept: dict[str, int] = {}
    for domain, gen in _GENERATORS.items():
        per_base: dict[str, int] = {}
        n = attempts = 0
        while n < n_per_domain and attempts < n_per_domain * 200:
            attempts += 1
            q = gen(rng)
            base = q.metadata.get("base")
            if base is not None and per_base.get(base, 0) >= max_per_base:
                continue
            if ndf.accept(q.prompt):
                queries.append(q)
                n += 1
                if base is not None:
                    per_base[base] = per_base.get(base, 0) + 1
        kept[domain] = n
    rng.shuffle(queries)
    return Dataset(f"delphi-trials-v2-{n_per_domain}x4-seed{seed}", tuple(queries)), kept
