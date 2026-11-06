# External datasets

`deepset_prompt_injections_{train,test}.csv`: [deepset/prompt-injections](https://huggingface.co/datasets/deepset/prompt-injections),
licensed Apache-2.0 by deepset. Converted unchanged to `text,label` (1 = injection). The train split
is used by `ml.training.train_injection_classifier --extra-csv`; the test split only by
`ml.evaluation.evaluate_real_injection`.
