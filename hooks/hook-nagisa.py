"""PyInstaller collection hook for the nagisa Japanese NLP package.

nagisa is a runtime dependency of qwen_asr (forced-aligner word tokenization).
It carries model data files (~46 MB) that Tagger() reads at import time from
the package directory; collect_all ensures those files reach the frozen bundle.

The Python 2-style bare absolute imports in nagisa/train.py (import prepro,
model, mecab_system_eval, tagger) are handled separately by the runtime hook
hooks/rthook-nagisa-compat.py, which stubs nagisa.train before nagisa's
__init__.py loads.  This file only handles collection.
"""

try:
    from PyInstaller.utils.hooks import collect_all  # type: ignore

    tmp_ret = collect_all('nagisa')
    datas, binaries, hiddenimports = tmp_ret[0], tmp_ret[1], tmp_ret[2]

    # Ensure the inference sub-modules are present in the PYZ even when
    # PyInstaller's static analyser misses them (nagisa/train.py uses bare
    # absolute import names that the analyser cannot resolve).
    hiddenimports += [
        'nagisa.prepro',
        'nagisa.mecab_system_eval',
        'nagisa.tagger',
        'nagisa.model',
    ]

except ImportError:
    import logging
    logging.info("PyInstaller not found, skipping hook-nagisa")
