Simple script solution for converting and importing DS Wizard questionnaires (DMPs) as RDA Common maDMP 1.2 JSON records into an external Elastic index, for use with an API or other machine actionable services, such as https://github.com/ChalmersLibrary/cth-dmps-api.

Requirements:
* An instance of Data Stewardship Wizard, version 4.x, with full access (admin) to the API (/wizard-api).
* Elasticsearch, version 6.x. Might require some adjustments if using a more recent version (see Known problems below).
* Python 3.x

Configuration:
* Paths to individual DMP parts are set in dsw2es.conf. This - and probably other things in the executable (dsw2es.py) - will need to be adjusted according to the knowledge model(s) used in DSW.
* URLs and login credentials are set in a .env file (use env_examples.txt for this). 

Known problems:
* Different versions of python elasticsearch module can cause errors like: TypeError: index() got an unexpected keyword argument 'document'.  
Using elasticsearch-7.16.3 should work.
* Encoding errors (Linux only?) can be handled by running this script as: 
PYTHONIOENCODING=utf-8 python3 dsw2es.py
