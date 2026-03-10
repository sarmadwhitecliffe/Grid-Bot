---
description: "Invoke Security Auditor for API key leakage, secrets management, and deployment safety"
agent: "@security-auditor"
tools: ["read", "grep", "glob", "bash"]
applies-to: "Grid Bot security validation and deployment gates"
---

# Security Audit Workflow

Invoke the Security Auditor agent for Grid Bot security validation before deployment.

## Audit Type

Select the appropriate audit for your needs:

**Audit Type**: `${input:AuditType|Pre-Commit Quick Scan,Pre-Phase Deep Scan,Pre-Testnet Deployment,Pre-Live Trading}`

## Quick Security Scan

For **Pre-Commit Quick Scan**, provide:
- **Files Changed**: `${input:FilesChanged}`

Performs quick scan focusing on:
1. Hardcoded API keys or secrets in changed files
2. .env is in .gitignore
3. Sensitive data in log statements

## Phase Completion Scan  

For **Pre-Phase Deep Scan**, provide:
- **Phase Name**: `${input:PhaseName}`
- **Additional Files**: `${input:AdditionalFiles}`

Full audit scope includes secrets detection, environment validation, testnet enforcement, rate limiting, error handling, logging safety, order guards, state security, and dependency audit.

## Deployment Security Gates

For **Pre-Testnet Deployment**, provide:
- **Target Exchange**: `${input:ExchangeName}`
- **Trading Pair**: `${input:TradingPair}`
- **Max Capital**: `${input:MaxCapital}`

For **Pre-Live Trading**, provide:
- **Initial Capital**: `${input:InitialCapital}`
- **Order Size**: `${input:OrderSize}`
- **Stop Loss**: `${input:StopLossPct}`

**Critical validation required for live trading - this is a deployment gate for real funds.**

## Prompt Template: Pre-Commit Quick Scan

```
@security-auditor

Perform a quick pre-commit security scan for the Grid Bot project.

Focus on:
1. Scanning for hardcoded API keys or secrets in [LIST FILES CHANGED]
2. Verify .env is in .gitignore
3. Check for sensitive data in log statements

Files to audit: [LIST SPECIFIC FILES OR "all source files"]

Report any critical issues blocking commit.
```

## Prompt Template: Pre-Phase Deep Scan

```
@security-auditor

Perform a deep security audit for Grid Bot [PHASE NAME] before completion.

Full audit scope:
1. **Secrets detection** — Scan all source files for hardcoded credentials
2. **Environment validation** — Check .env.example is current and .env is gitignored
3. **Testnet enforcement** — Verify TESTNET flag is documented and enforced
4. **Rate limiting** — Confirm enableRateLimit=True on all ccxt instances
5. **Error handling** — Check no broad Exception catches that mask issues
6. **Logging safety** — Ensure no sensitive data in logs
7. **Order guards** — Verify risk manager validates orders before placement
8. **State security** — Check no secrets in grid_state.json
9. **Dependency audit** — Check for known vulnerabilities in requirements.txt

Files to audit:
- config/settings.py
- src/exchange/exchange_client.py
- src/oms/order_manager.py
- src/persistence/state_store.py
- [ADD OTHER PHASE-SPECIFIC FILES]

Provide detailed report with file:line references for any issues.
```

## Prompt Template: Pre-Testnet Deployment

```
@security-auditor

Perform production readiness security audit before Grid Bot testnet deployment.

**Critical validation required:**
1. ✅ TESTNET environment variable is set to "true"
2. ✅ All API keys are from testnet accounts (not production)
3. ✅ Order size limits are appropriate for testing
4. ✅ Emergency stop mechanism is functional
5. ✅ Telegram alerts are configured and tested
6. ✅ State persistence and recovery have been tested
7. ✅ No production exchange URLs hardcoded
8. ✅ Risk manager validates all orders
9. ✅ Dependency vulnerabilities resolved

Deployment target: [Testnet Exchange Name]
Trading pair: [SYMBOL]
Max capital exposure: [AMOUNT]

**This is a DEPLOYMENT GATE — fail audit if any critical issue found.**

Provide deployment clearance decision: APPROVED / BLOCKED with reasons.
```

## Prompt Template: Pre-Live Trading

```
@security-auditor

Perform FINAL security audit before enabling Grid Bot live trading with real funds.

**User confirmation required:**
- [ ] User has reviewed and acknowledged trading risks
- [ ] User confirms capital limits are appropriate
- [ ] User has tested bot on testnet successfully
- [ ] User has verified exchange API permissions are correct (no withdrawals)

**Security validation:**
1. ✅ TESTNET flag is set to "false" (live trading mode)
2. ✅ API keys have restricted permissions (trading only, no withdrawals)
3. ✅ Stop-loss and MAX_DRAWDOWN_PCT are configured
4. ✅ ORDER_SIZE_QUOTE is set to safe percentage of capital
5. ✅ Emergency stop mechanism tested
6. ✅ Monitoring and alerting are enabled
7. ✅ State backup and recovery procedures documented
8. ✅ Circuit breakers (ADX regime switching) are active

**Risk Management:**
- Initial capital: [AMOUNT]
- Max position size: [ORDER_SIZE_QUOTE]
- Stop-loss trigger: [MAX_DRAWDOWN_PCT]
- Trading pair: [SYMBOL]
- Exchange: [EXCHANGE_ID]

**This is a CRITICAL DEPLOYMENT GATE for LIVE FUNDS.**

Provide final deployment clearance: APPROVED FOR LIVE TRADING / BLOCKED with reasons.

Require explicit user confirmation: "I understand the risks and approve live trading."
```

## Example Usage

### Example 1: Quick pre-commit check
```
@security-auditor

Quick scan before commit.

Files changed:
- src/oms/order_manager.py (added retry logic)
- tests/test_order_manager.py (added tests)

Check for hardcoded secrets and logging safety.
```

### Example 2: Phase 1 completion
```
@security-auditor

Deep security audit for Grid Bot Phase 1 completion.

Phase 1 modules:
- config/settings.py
- config/grid_config.yaml
- src/exchange/exchange_client.py
- src/data/price_feed.py
- tests/test_settings.py
- tests/test_exchange_client.py
- tests/test_price_feed.py

Full audit including secrets, rate limiting, error handling, and testnet enforcement.
```

### Example 3: Before testnet deployment
```
@security-auditor

Pre-testnet deployment security gate.

Target: Binance Testnet
Pair: BTC/USDT
Capital: $1000 (test funds)

Verify TESTNET=true and all safety mechanisms active.

DEPLOYMENT GATE — must pass to proceed.
```

---

## Security Audit Checklist

Use this checklist with the Security Auditor:

### Pre-Commit (Quick)
- [ ] No hardcoded API keys in changed files
- [ ] No sensitive data in new log statements
- [ ] .env changes not committed

### Pre-Phase (Deep)
- [ ] All secrets via environment variables
- [ ] .env.example up to date
- [ ] Rate limiting enabled everywhere
- [ ] Error handling is specific (no bare except)
- [ ] No sensitive data in logs or state files
- [ ] Order guards in place

### Pre-Testnet (Production Readiness)
- [ ] TESTNET flag documented and set
- [ ] API keys are testnet-only
- [ ] Capital limits appropriate
- [ ] Emergency stop tested
- [ ] Monitoring enabled
- [ ] State recovery tested

### Pre-Live (Critical Gate)
- [ ] User confirmation obtained
- [ ] API permissions verified (no withdrawals)
- [ ] Stop-loss configured
- [ ] Risk limits set appropriately
- [ ] All systems tested on testnet
- [ ] Backup procedures documented

---

**Remember:** For cryptocurrency trading, security audits are mandatory before deployment. Never skip these gates.
