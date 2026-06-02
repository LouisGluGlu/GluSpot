# Font Assets

`格律宋散` requires Source Han Serif SC (`思源宋体`) and intentionally does not fall back to another typeface.

Development and packaging should use Adobe's official Source Han Serif repository:

- https://github.com/adobe-fonts/source-han-serif
- License: SIL Open Font License 1.1, as documented by the upstream repository.

The packaged build should include the Simplified Chinese Source Han Serif SC OTF/OTC asset and load it before rendering `格律宋散`.
