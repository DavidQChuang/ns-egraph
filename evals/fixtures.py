"""Build the isolated synthetic graph corpus for each evaluation run."""

from pathlib import Path
from shutil import copyfile

from egraph import Graph
from recall import checkpoint


def prepare(root: Path) -> None:
    example = Path(__file__).resolve().parents[1] / "examples" / "greenhouse.json"
    copyfile(example, root / example.name)

    dispatch = Graph.new("Dispatch handoff", "Where should the late crate be delivered?")
    goal = dispatch.add("goal", "Establish the receiving location")
    question = dispatch.add("question", "Which stop receives the late crate?", derives_from=[goal])
    source = dispatch.add("source", "Dispatch schedule")
    observation = dispatch.add("observation", "Courier unloads the crate at Dock C",
                               {"evidence": "Dock C receives the late crate at 17:30.",
                                "aliases": ["evening crate"]}, cites=[source])
    claim = dispatch.add("claim", "Dock C is the handoff point", supports=[observation])
    dispatch.link("answers", observation, question)
    dispatch.set_state(question, "answered")
    edge = dispatch.link("supports", claim, goal)
    dispatch.accept(goal, [edge], "The dispatch schedule specifies Dock C.")
    access = dispatch.add("work", "Confirm Dock C access with the receiving team", derives_from=[claim])
    dispatch.add("work", "Check tomorrow's dispatch notices", derives_from=[question])
    dispatch.save(root / "dispatch.json")

    gallery = Graph.new("Gallery condensation", "Why did the display cases fog up?")
    goal = gallery.add("goal", "Choose an intervention for the display cases")
    question = gallery.add("question", "Which event preceded the case fogging?", derives_from=[goal])
    source = gallery.add("source", "Environmental logger")
    observation = gallery.add("observation", "Case humidity rose during the humidifier cycle",
                              {"evidence": "The initial logger report showed 79% relative humidity."},
                              cites=[source])
    claim = gallery.add("claim", "The humidifier schedule explains case fogging", supports=[observation])
    edge = gallery.link("supports", claim, goal)
    gallery.accept(goal, [edge], "The reading coincided with the scheduled cycle.")
    gallery.link("answers", observation, question)
    gallery.revise(observation, {"evidence": "The corrected logger report showed 58% relative humidity."})
    witness = gallery.add("observation", "Condensation appeared at the entrance",
                          {"evidence": "A visitor reported dripping at the entrance."})
    gallery.retract(witness, "The visitor was describing a different site.")
    gallery.save(root / "gallery.json")

    checkpoint(root, Path("greenhouse.json"), "garden-session", "q-11", False)
    checkpoint(root, Path("dispatch.json"), "dispatch-session", access, False)
    checkpoint(root, Path("gallery.json"), "closed-session", None, True)
