# VMPower agent

Collects login data for your virtual machines and reports them to your VMPower account. Read the documentation [here](https://docs.vmpower.com/VMPower%20agent%20%5Bbeta%5D/Getting-Started/).

# Developing

These are the instructions for developing this package. Only follow these if you need to make a change to the agent.
 
## Getting started

```bash
# Install dependencies
npm i
# Transpile the code
npm run build
# Run the CLI
node build/index.js
```

## Running the tests

```bash
# Runs flow typechecks
npm run flow
# Runs the functional test suite
npm run test
# Runs the CI checks. Do this before a PR
npm run ci_validate
```
