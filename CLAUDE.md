## Development Practices
Always identify and use software development best practices (like DRY) for any code changes. Avoid redundant code where possible. Build reusable components where feasible.

## Planning approval
Before implementing any change — new features, refactors touching multiple files, schema changes, new agents, or anything that would take more than a few edits — present a plan and wait for explicit approval. Do not start coding until the user confirms. What counts as "large": adding a new route + service + model, changing shared infrastructure (event bus, agent registry, database schema), or any task that touches more than ~3 files in a meaningful way. 

## Fix quality
When recommending fixes, check if this is the right fix for the solution or just a mask on the real problem, a band-aid. If it is not the right fix, don't implement it, and clearly state that the right fix as well as the band-aid, so that the I can make a choice.

### Avoid redundancy
Before implementing any new functionality, check if it does not exist already, and if so, reuse existing code. 

## Testing
Give clear instructions to the user about how they can validate the change.