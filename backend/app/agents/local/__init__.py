"""Local signals agents: roads, schools, public safety, news, under a coordinator.

Agents only extract facts from allowlisted sources (sources/registry.yaml); code validates every
fact against the source text, and rules/local.py decides what facts can change."""
