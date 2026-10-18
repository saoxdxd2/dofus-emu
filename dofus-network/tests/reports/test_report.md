# dofus-network Test Report

Generated: 2026-10-18T15:20:58+00:00

## Summary

- Tests run: 73
- Successful: 71
- Failures: 0
- Errors: 2
- Skipped: 12
- Overall: FAIL

## Errors

### test_transformer_forward (test_ai.TestDofusBrainTransformer.test_transformer_forward)
```
Traceback (most recent call last):
  File "C:\Users\sao\Documents\dofus-emu\dofus-network\tests\test_ai.py", line 48, in test_transformer_forward
    model = DofusBrainTransformer(vocab_size=100, d_model=64, n_layers=2, n_heads=4)
TypeError: DofusBrainTransformer.__init__() got an unexpected keyword argument 'n_layers'. Did you mean 'num_layers'?

```

### test_trm_moe_parameter_count (test_performance.TestPerformanceBaseline.test_trm_moe_parameter_count)
```
Traceback (most recent call last):
  File "C:\Users\sao\Documents\dofus-emu\dofus-network\tests\test_performance.py", line 39, in test_trm_moe_parameter_count
    model = TRMMoE(cfg)
            ^^^^^^
NameError: name 'TRMMoE' is not defined

```
