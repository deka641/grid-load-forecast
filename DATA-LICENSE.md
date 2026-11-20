# Data licence

The [MIT License](LICENSE) covers the source code of this project only. It does not cover data.

The project processes data published by the Bundesnetzagentur on [SMARD.de](https://www.smard.de). SMARD data is licensed under the [Creative Commons Attribution 4.0 International licence (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). Required attribution: "Bundesnetzagentur | SMARD.de".

## Wholesale prices

The Bundesnetzagentur reviews the licensing of the wholesale electricity prices shown on SMARD.de in its HEDWIG procedure. Until that review is concluded, this project uses price data with that reservation. Price data can be switched off with `GRIDLOAD_INCLUDE_PRICES=false`: the pipeline then stops loading prices and removes them from the data it publishes, including prices loaded earlier. The repository contains only a small excerpt of price data as test fixtures.

## Data in this repository

Test fixtures recorded from SMARD are excerpts of SMARD data and fall under CC BY 4.0 with the attribution above.
