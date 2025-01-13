#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import json
import sys
import elasticsearch
import uuid as uuid
from elasticsearch import Elasticsearch
import os
from dotenv import load_dotenv
import logging
import configparser
from datetime import datetime
import uuid

# Read variables from dotenv
load_dotenv()
esurl = os.getenv("ELASTIC_URL")
esport = int(os.getenv("ELASTIC_PORT"))
esindex = os.getenv("ELASTIC_INDEX_NAME")
esuser = os.getenv("ELASTIC_USER")
espw = os.getenv("ELASTIC_PW")
dswurl = os.getenv("DSW_URL")
dswuser = os.getenv("DSW_USER")
dswpw = os.getenv("DSW_PW")
logfile = os.getenv("LOGFILE")
baseurl = os.getenv("BASEURL")

# Read config
config = configparser.ConfigParser()
config.read_file(open(r'dsw2es.conf'))

# Create and configure logger
logging.basicConfig(filename=logfile,
                    format='%(asctime)s %(message)s',
                    filemode='a')

# Creating an object
logger = logging.getLogger()

# Setting the threshold of logger to DEBUG
logger.setLevel(logging.WARNING)

logger.warning('Trying to update the ' + esindex + ' index.')

# Authorize with DSW
dsw_token = ''
try:
    dsw_authurl = dswurl + '/tokens'
    auth_data = dict(email=dswuser, password=dswpw)
    data_auth = requests.post(url=dsw_authurl, json=auth_data, headers={'Accept': 'application/json'}).text
    data_auth = json.loads(data_auth)
    dsw_token = data_auth['token']
except requests.exceptions.HTTPError as e:
    print('Could not authenticate with DSW, user: ' + dswuser + ' , existing.')
    logger.error('Could not authenticate with DSW, user: ' + dswuser + ' , exiting: ' + e.response.text)
    sys.exit(1)

headers = {'Accept': 'application/json',
           'Authorization': 'Bearer ' + dsw_token}

# Create new index (or replace existing)
# Require elasticsearch >=7.16.3 to work with ES 6.x
elastic = Elasticsearch([{'host': esurl, 'port': esport, 'use_ssl': True}], http_auth=(esuser, espw))
try:
    create_resp = elastic.indices.create(index=esindex, ignore=[400, 404])

    # print(create_resp)
    logger.info('Index ' + esindex + ' was successfully created.')
    print('Index ' + esindex + ' was successfully created.')
except elasticsearch.exceptions.RequestError as e:
    if e.error == 'resource_already_exists_exception':
        # print('We will ignore this')
        pass  # Index already exists. Ignore, it will be recreated.
    else:  # Other exception - raise it
        logger.error('Index ' + esindex + ' could not be created: ' + e.error)
        print('Index ' + esindex + ' could not be created: ' + e.error)
        raise e
        sys.exit()

# Request data from DSW as string
dsw_geturl = dswurl + '/questionnaires?isTemplate=false&sort=createdAt%2Cdesc&size=500'
data = requests.get(url=dsw_geturl, headers=headers).text

# convert string to Json
data = json.loads(data)

# debug
# print(data)

dmp = {}
count = 0
madmp_schema = "https://github.com/RDA-DMP-Common/RDA-DMP-Common-Standard/tree/master/examples/JSON/JSON-schema/1.1"

for i in data['_embedded']['questionnaires']:
    import_this = 'true'
    is_outdated = 'false'
    d = dict()
    md = dict()
    d['schema'] = madmp_schema
    dmp_id = i['uuid']
    created_at = i['createdAt']
    if i['updatedAt']:
        updated_at = i['updatedAt']
        d['modified'] = updated_at
    if 'state' in i:
        state = i['state']
        if state == 'Outdated':
            is_outdated = 'true'

    is_template = i['isTemplate']
    dmp_name = i['name']

    # Which DMPs to include in index?
    rules = [not is_template,
             is_outdated == 'false']

    if all(rules):
        print('{}, {}'.format(dmp_id, dmp_name))
        # Add to json output

        d['title'] = dmp_name
        d['created'] = created_at
        di = {"identifier": baseurl + dmp_id, "type": "url"}

        d['dmp_id'] = di

        if i['package']:
            d[
                'description'] = "This DMP has been created using Chalmers Data Stewardship Wizard (dsw.chalmers.se) " \
                                 "and is based on the knowledge model " + \
                                 i['package']['name'] + " (" + i['package']['id'] + ")."
            if "swe" in i['package']['id']:
                d['language'] = 'swe'
            else:
                d['language'] = 'eng'

        md['id'] = dmp_id
        if state:
            md['state'] = state
        if 'visibility' in i:
            md['visibility'] = i['visibility']
        if 'sharing' in i:
            md['sharing'] = i['sharing']
        if 'description' in i:
            md['description'] = i['description']

        # request full doc from DSW
        link_full = dswurl + '/questionnaires/' + str(dmp_id) + '/questionnaire'

        # debug
        # print ('link full: {}'.format(link_full))

        # Retrieve full DMP record from API
        try:
            data_full = requests.get(url=link_full, headers=headers).text
            data_full = json.loads(data_full)
        except requests.exceptions.HTTPError as e:
            print('Could not retrieve record id: ' + dmp_id + ' , existing.')
            logger.error('Could not retrieve record id: ' + dmp_id + ' , existing: ' + e.response.text)
            sys.exit(1)

        # Disclaimer
        if config.get('Paths', 'disclaimer') in data_full['replies']:
            disclaimer_replies_node = config.get('Paths', 'disclaimer')
            disclaimer_answer = data_full['replies'][disclaimer_replies_node]['value']['value']
            print('disclaimer path: ' + str(disclaimer_answer))
            if disclaimer_answer == config.get('Paths', 'disclaimer_answer_no'):
                print('User has rejected the disclaimer for dmp id: ' + str(dmp_id) + '!')
                md['disclaimer_allow_sharing'] = 'no'
            elif disclaimer_answer == config.get('Paths', 'disclaimer_answer_yes'):
                print('User has approved the disclaimer for dmp id: ' + str(dmp_id) + '!')
                md['disclaimer_allow_sharing'] = 'yes'
            else:
                print('User has not responded to the disclaimer for dmp id: ' + str(dmp_id) + '!')
                md['disclaimer_allow_sharing'] = 'missing / not answered'
        else:
            print('User has not responded to the disclaimer for dmp id: ' + str(dmp_id) + '!')
            md['disclaimer_allow_sharing'] = 'missing / not answered'

        # Contact and contributor(s)

        if config.get('Paths', 'contributors') in data_full['replies']:
            contributors = data_full['replies'][
                config.get('Paths', 'contributors')]

            if contributors:
                cs = []
                cc = {}
                for c in contributors['value']['value']:
                    ct = {}
                    affiliation_node = ''

                    contributor = config.get('Paths', 'contributors') + '.' + c
                    try:
                        name_node = contributor + "." + config.get('Paths', 'contributor.name')
                        contributor_name = data_full['replies'][name_node]['value']['value']
                        ct["name"] = contributor_name
                    except KeyError:
                        contributor_name = ''
                    try:
                        email_node = contributor + "." + config.get('Paths', 'contributor.email')
                        contributor_email = data_full['replies'][email_node]['value']['value']
                        ct["mbox"] = contributor_email
                    except KeyError:
                        contributor_email = ''
                    try:
                        orcid_node = contributor + "." + config.get('Paths', 'contributor.orcid')
                        contributor_orcid = data_full['replies'][orcid_node]['value']['value']
                        ct["contributor_id"] = {"identifier": contributor_orcid, "type": "orcid"}
                    except KeyError:
                        contributor_orcid = ''
                    try:
                        ct["affiliation"] = {}
                        affiliation_node = contributor + '.' + config.get('Paths', 'contributor.affiliation');
                        # print('aff node: ' + affiliation_node)
                        # Non-standard field (CTH), separate field for Horizon Europe KM

                        if affiliation_node + '.' + config.get('Paths', 'contributor.affiliation.cth') in \
                                data_full['replies']:
                            ct["affiliation"] = {"name": "Chalmers University of Technology",
                                                 "affiliation_id": {"name": "https://ror.org/040wg7k59", "type": "ror"}}
                        if affiliation_node in data_full['replies'] and data_full['replies'][affiliation_node]['value'][
                            'value'] == config.get('Paths', 'contributor.affiliation.gu'):
                            ct["affiliation"] = {"name": "University of Gothenburg",
                                                 "affiliation_id": {"name": "https://ror.org/01tm6cn81", "type": "ror"}}
                        if affiliation_node + '.' + config.get('Paths', 'contributor.affiliation.other') in \
                                data_full['replies']:
                            affiliation_other = affiliation_node + '.' + config.get('Paths',
                                                                                    'contributor.affiliation.other')
                            affiliation_other_name = data_full['replies'][affiliation_other]['value']['value']['value']
                            affiliation_other_id = data_full['replies'][affiliation_other]['value']['value']['id']
                            ct["affiliation"] = {"name": affiliation_other_name,
                                                 "affiliation_id": {"name": affiliation_other_id, "type": "ror"}}
                        # HE
                        if 'root-he' in data_full['packageId']:
                            if affiliation_node in data_full['replies']:
                                affiliation_he = affiliation_node
                                affiliation_he_name = data_full['replies'][affiliation_he]['value']['value'][
                                    'value']
                                affiliation_he_id = data_full['replies'][affiliation_he]['value']['value']['id']
                                ct["affiliation"] = {"name": affiliation_he_name,
                                                     "affiliation_id": {"name": affiliation_he_id, "type": "ror"}}
                                print("aff_node_he: " + affiliation_he)
                            # print('aff: ' + contributor_name + ', ' + str(ct['affiliation']))
                    except KeyError:
                        print('no affiliations')
                        ct["affiliation"] = {}
                    try:
                        # default value, todo: multipart roles in HE KM
                        contributor_role = 'contact person'
                        role_node = contributor + "." + config.get('Paths', 'contributor.roles')
                        role_id = data_full['replies'][role_node]['value']['value']
                        print("role: " + str(role_id))

                        if role_id == config.get('Paths', 'contributor.role.contact'):
                            contributor_role = 'contact person'
                            if contributor_orcid:
                                cc['contact_id'] = {"identifier": contributor_orcid, "type": "orcid"}
                            cc['affiliation'] = ct['affiliation']
                            cc['name'] = contributor_name
                            if contributor_email:
                                cc['mbox'] = contributor_email
                        elif role_id == config.get('Paths', 'contributor.role.datacollector'):
                            contributor_role = 'data collector'
                        elif role_id == config.get('Paths', 'contributor.role.datacurator'):
                            contributor_role = 'data curator'
                        elif role_id == config.get('Paths', 'contributor.role.datasteward'):
                            contributor_role = 'data steward'
                        elif role_id == config.get('Paths', 'contributor.role.researcher'):
                            contributor_role = 'researcher'
                        elif role_id == config.get('Paths', 'contributor.role.other'):
                            contributor_role = 'other'
                        # HE
                        if config.get('Paths', 'contributor.role_he.contact') in role_id:
                            cc = {}
                            contributor_role = 'contact person'
                            if contributor_orcid:
                                cc['contact_id'] = {"identifier": contributor_orcid, "type": "orcid"}
                            cc['affiliation'] = ct['affiliation']
                            cc['name'] = contributor_name
                            if contributor_email:
                                cc['mbox'] = contributor_email
                        # else:
                        #    contributor_role = 'other'
                    except KeyError:
                        contributor_role = '(unknown)'
                    ct["role"] = [contributor_role]

                    cs.append(ct)

            d["contributor"] = cs
            if cc:
                d["contact"] = cc
                md['hasContactPerson'] = 'true'
            else:
                md['hasContactPerson'] = 'false'

        # Projects and funding

        md['hasProject'] = 'false'

        if config.get('Paths', 'projects') in data_full['replies']:
            projects = data_full['replies'][config.get('Paths', 'projects')]

            if projects:
                ps = []
                md['hasProject'] = 'true'
                for p in projects['value']['value']:
                    pt = {}
                    project = config.get('Paths', 'projects') + '.' + p
                    try:
                        pname_node = project + "." + config.get('Paths', 'project.name')
                        pname = data_full['replies'][pname_node]['value']['value']
                        pt["title"] = pname
                    except KeyError:
                        pname = ''
                    try:
                        pdesc_node = project + "." + config.get('Paths', 'project.desc')
                        pdesc = data_full['replies'][pdesc_node]['value']['value']
                        pdesc = pdesc.replace('\n', ' ')
                        pt["description"] = pdesc
                    except KeyError:
                        pdesc = ''
                    try:
                        pacronym_node = project + "." + config.get('Paths', 'project.acronym')
                        pacronym = data_full['replies'][pacronym_node]['value']['value']
                        pt["acronym"] = pacronym
                    except KeyError:
                        pacronym = ''
                    try:
                        pstart_node = project + "." + config.get('Paths', 'project.start')
                        pstart = data_full['replies'][pstart_node]['value']['value']
                        # Modify date format if necessary
                        if len(pstart) > 10:
                            pstart = pstart[0:10]
                        pt["start"] = pstart
                    except KeyError:
                        pstart = ''
                    try:
                        pend_node = project + "." + config.get('Paths', 'project.end')
                        pend = data_full['replies'][pend_node]['value']['value']
                        # Modify date format if necessary
                        if len(pend) > 10:
                            pend = pend[0:10]
                        pt["end"] = pend
                    except KeyError:
                        pend = ''
                    try:
                        pfunding_node = project + "." + config.get('Paths', 'project.funding')
                        pfl = []
                        for pf in data_full['replies'][pfunding_node]['value']['value']:
                            pfn = {}
                            funding = pfunding_node + '.' + pf
                            # print('funding: ' + funding)
                            try:
                                pf_funder_node = funding + "." + config.get('Paths', 'project.funder')
                                pf_funder = data_full['replies'][pf_funder_node]['value']['value']['id']
                                pf_funder_name = data_full['replies'][pf_funder_node]['value']['value']['value']
                                pfn['funder_name'] = pf_funder_name
                                if pf_funder_name == 'Vetenskapsrådet':
                                    pfn['funder_id'] = {'identifier': 'https://ror.org/03zttf063', 'type': 'other'}
                                else:
                                    pfn['funder_id'] = {'identifier': pf_funder, 'type': 'url'}
                            except KeyError:
                                pfunder = ''
                            try:
                                pf_grant_node = funding + "." + config.get('Paths', 'project.funder.grant')
                                pf_grant = data_full['replies'][pf_grant_node]['value']['value']
                                pfn['grant_id'] = {'identifier': pf_grant, 'type': 'other'}
                            except KeyError:
                                pfunder = ''
                            try:
                                pf_status_node = funding + "." + config.get('Paths', 'project.funding.status')
                                pf_status_id = data_full['replies'][pf_status_node]['value']['value']
                                if pf_status_id == config.get('Paths', 'project.funding.status.granted'):
                                    pf_status = 'granted'
                                elif pf_status_id == config.get('Paths', 'project.funding.status.applied'):
                                    pf_status = 'applied'
                                elif pf_status_id == config.get('Paths', 'project.funding.status.planned'):
                                    pf_status = 'planned'
                                else:
                                    pf_status = 'unknown'
                                pfn['funding_status'] = pf_status
                            except KeyError:
                                pf_status_id = ''

                            pfl.append(pfn)

                        pt['funding'] = pfl
                    except KeyError:
                        print('no funding')

                    try:
                        if pt['funding']:
                            ps.append(pt)
                    except KeyError:
                        print('nothing to add')

            d['project'] = ps

        # Ethical issues

        ethical_issues_exist = 'unknown'

        # GDPR
        if config.get('Paths', 'ethical_issues') in data_full['replies']:
            if \
                    data_full['replies'][config.get('Paths', 'ethical_issues')][
                        'value']['value'] == config.get('Paths', 'ethical_issues.yes'):
                ethical_issues_exist = 'yes'
                md['hasPersonalData'] = 'true'
                if config.get('Paths', 'ethical_issues.desc') in \
                        data_full['replies']:
                    ethical_issues_desc = data_full['replies'][
                        config.get('Paths', 'ethical_issues.desc')][
                        'value']['value']
                    d['ethical_issues_description'] = ethical_issues_desc
            if \
                    data_full['replies'][config.get('Paths', 'ethical_issues')][
                        'value'][
                        'value'] == config.get('Paths', 'ethical_issues.no'):
                ethical_issues_exist = 'yes'
                ethical_issues_exist = 'no'

        d['ethical_issues_exist'] = ethical_issues_exist

        # GDPR (Horizon Europe KM)
        if config.get('Paths', 'ethical_issues_he') in data_full['replies']:
            if \
                    data_full['replies'][config.get('Paths', 'ethical_issues_he')][
                        'value']['value'] == config.get('Paths', 'ethical_issues_he.yes'):
                ethical_issues_exist = 'yes'
                md['hasPersonalData'] = 'true'
                if config.get('Paths', 'ethical_issues_he.desc') in \
                        data_full['replies']:
                    ethical_issues_desc = data_full['replies'][
                        config.get('Paths', 'ethical_issues_he.desc')][
                        'value']['value']
                    d['ethical_issues_description'] = ethical_issues_desc
            if \
                    data_full['replies'][config.get('Paths', 'ethical_issues_he')][
                        'value'][
                        'value'] == config.get('Paths', 'ethical_issues_he.no'):
                ethical_issues_exist = 'no'
                ethical_issues_exist = 'no'

        d['ethical_issues_exist'] = ethical_issues_exist

        # Dataset(s)

        md['hasDatasets'] = 'false'

        if 'd5b27482-b598-4b8c-b534-417d4ad27394.4e0c1edf-660c-4ebf-81f5-9fa959dead30' in data_full['replies']:
            datasets = data_full['replies']['d5b27482-b598-4b8c-b534-417d4ad27394.4e0c1edf-660c-4ebf-81f5-9fa959dead30']

            if datasets:
                dsts = []
                md['hasDatasets'] = 'true'
                dstname = ''
                for dst in \
                        data_full['replies'][
                            'd5b27482-b598-4b8c-b534-417d4ad27394.4e0c1edf-660c-4ebf-81f5-9fa959dead30'][
                            'value']['value']:

                    dset = {}
                    dataset = 'd5b27482-b598-4b8c-b534-417d4ad27394.4e0c1edf-660c-4ebf-81f5-9fa959dead30.' + dst
                    try:
                        dstname_node = dataset + ".b0949d09-d179-4491-9fb4-14b0deb9f862"
                        dstname = data_full['replies'][dstname_node]['value']['value']
                        dset["title"] = dstname
                    except KeyError:
                        dstname = ''
                    try:
                        dstdesc_node = dataset + ".205a886d-83d7-4359-ae63-7103e05357c3"
                        dstdesc = data_full['replies'][dstdesc_node]['value']['value']
                        dset["description"] = dstdesc
                    except KeyError:
                        dstdesc = ''
                    try:
                        dstpersdata_node = dataset + ".a1d76760-053c-4706-80a2-cfb6c6a061f3"
                        dstpersdata_id = data_full['replies'][dstpersdata_node]['value']['value']
                        if dstpersdata_id == '0cdc4817-7c54-4ec1-b2f4-5c007a85c7b8':
                            dstpersdata = 'yes'
                        elif dstpersdata_id == '4b2a08c7-4942-41fc-8114-d3868c882624':
                            dstpersdata = 'no'
                        else:
                            dstpersdata = 'unknown'
                        dset['personal_data'] = dstpersdata
                    except KeyError:
                        dstpersdata = ''
                    try:
                        dstsensdata_node = dataset + ".cc95b399-7d8d-4232-bccf-686f78c91bff"
                        dstsensdata_id = data_full['replies'][dstsensdata_node]['value']['value']
                        if dstsensdata_id == '2686575d-cd74-4e2c-8524-eaca6f510425':
                            dstsensdata = 'yes'
                        elif dstsensdata_id == '60de66a3-d303-4784-8931-bc58f8a3e747':
                            dstsensdata = 'no'
                        else:
                            dstsensdata = 'unknown'
                        dset['sensitive_data'] = dstsensdata
                    except KeyError:
                        dstsensdata = ''
                    dset["dataset_id"] = {'identifier': str(uuid.uuid4()), 'type': 'other'}
                    try:
                        dstid_node = dataset + ".cf727a0a-78c4-45a7-aa9b-cf7650ae873a"
                        for dist in data_full['replies'][dstid_node]['value']['value']:
                            dset_dist = {}
                            dataset_dist = dataset + '.cf727a0a-78c4-45a7-aa9b-cf7650ae873a.' + dist
                            try:
                                dist_dset_id_type_node = dataset_dist + ".5c22cf59-89e3-43a1-af10-1af43a97bcb2"
                                dist_dset_id_type = data_full['replies'][dist_dset_id_type_node]['value']['value']
                                if dist_dset_id_type == '48062bc9-0ffb-4509-bec6-e90641a30569':
                                    dist_dset_type = 'doi'
                                elif dist_dset_id_type == 'b93a037a-006a-486f-87e0-6bef5c28879b':
                                    dist_dset_type = 'handle'
                                elif dist_dset_id_type == '7a1d3b28-5f85-48b8-b052-2448c276d9fc':
                                    dist_dset_type = 'url'
                                elif dist_dset_id_type == 'c353f027-823b-4242-9149-37dca26cf4bc':
                                    dist_dset_type = 'ark'
                                elif dist_dset_id_type == '97236701-7b62-40f8-99a0-3b18d3fe3658':
                                    dist_dset_type = 'other'
                                else:
                                    dist_dset_type = 'unknown'
                                dist_dset_id_node = dataset_dist + ".9e13b2d3-5f00-4e19-8a52-5c33c5b1cb07"
                                dist_dset_id = data_full['replies'][dist_dset_id_node]['value']['value']
                                dset["dataset_id"] = {'identifier': dist_dset_id, 'type': dist_dset_type}
                            except KeyError:
                                dist_dset_id = ''
                    except:
                        print()

                    # Only add dataset if there is a dataset title (correct?)
                    if dstname:
                        dsts.append(dset)

                    if dsts:
                        d['dataset'] = dsts
                        print('dataset added')
        else:
            print('NO DATASETS')
            # Create a generic (empty) set to comply with standard
            md['hasDatasets'] = 'false'
            dsts_empty = []
            dset_empty = {"type": 'dataset', "title": 'Generic dataset',
                          "description": 'No individual datasets have been defined for this DMP.',
                          "dataset_id": {'identifier': str(uuid.uuid4()), 'type': 'other'}, "sensitive_data": 'unknown',
                          "personal_data": 'unknown'}
            dsts_empty.append(dset_empty)
            d['dataset'] = dsts_empty
            print('generic (dummy) dataset added')

        # Extensions (local)

        md['indexed'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')

        # DMP owner (for now eq to (first named) admin)
        md['dmp_owner'] = {}
        for op in data_full['permissions']:
            # print(op)
            if 'ADMIN' in op['perms']:
                owner_name = op['member']['firstName'] + ' ' + op['member']['lastName']
                owner_uuid = op['member']['uuid']
                md['dmp_owner'] = {'name': owner_name, 'uuid': owner_uuid}
                break

        md['hasExistingData'] = 'false'
        if '82fd0cce-2b41-423f-92ad-636d0872045c.efc80cc8-8318-4f8c-acb7-dc1c60e491c1' in data_full['replies']:
            if \
                    data_full['replies']['82fd0cce-2b41-423f-92ad-636d0872045c.efc80cc8-8318-4f8c-acb7-dc1c60e491c1'][
                        'value'][
                        'value'] == '2663b978-5125-4224-9930-0a50dbe895c9':
                md['hasExistingData'] = 'true'

        md['hasCollectingNewData'] = 'false'
        if 'b1df3c74-0b1f-4574-81c4-4cc2d780c1af.f87c331d-794a-42c8-a910-61a2a9110dab' in data_full['replies']:
            if \
                    data_full['replies']['b1df3c74-0b1f-4574-81c4-4cc2d780c1af.f87c331d-794a-42c8-a910-61a2a9110dab'][
                        'value'][
                        'value'] == 'e4ca2d31-137a-46d3-96cd-3e9e8c5e9a76':
                md['hasCollectingNewData'] = 'true'

        md['hasCreatingNewData'] = 'false'
        if 'b1df3c74-0b1f-4574-81c4-4cc2d780c1af.0561d9e5-8bbe-49c7-a656-5714d3c94078' in data_full['replies']:
            if \
                    data_full['replies']['b1df3c74-0b1f-4574-81c4-4cc2d780c1af.0561d9e5-8bbe-49c7-a656-5714d3c94078'][
                        'value'][
                        'value'] == '65da7ae7-5062-4667-9cb2-86c5d17c4473':
                md['hasCreatingNewData'] = 'true'

        md['hasNonEquipmentNewData'] = 'false'
        if 'b1df3c74-0b1f-4574-81c4-4cc2d780c1af.f038bd46-ee4e-4f53-b7ea-482381c2c855' in data_full['replies']:
            if \
                    data_full['replies']['b1df3c74-0b1f-4574-81c4-4cc2d780c1af.f038bd46-ee4e-4f53-b7ea-482381c2c855'][
                        'value'][
                        'value'] == '4fd89b13-f33c-4858-8b25-ab6da271efc6':
                md['hasNonEquipmentNewData'] = 'true'

        if config.get('Paths', 'metadata.storage_needs') in data_full['replies']:
            storage_needs_id = \
                data_full['replies'][config.get('Paths', 'metadata.storage_needs')][
                    'value'][
                    'value']
            if storage_needs_id == config.get('Paths', 'metadata.storage_needs.very_small'):
                storage_needs = 'Very small, < 1 TB'
            elif storage_needs_id == config.get('Paths', 'metadata.storage_needs.small'):
                storage_needs = 'Small (1-5 TB)'
            elif storage_needs_id == config.get('Paths', 'metadata.storage_needs.large'):
                storage_needs = 'Large (5-50 TB)'
            elif storage_needs_id == config.get('Paths', 'metadata.storage_needs.very_large'):
                storage_needs = 'Very large (>= 50 TB)'
            else:
                storage_needs = 'unknown'
            md['storage_needs'] = storage_needs

        if 'root-he' in data_full['packageId']:
            if config.get('Paths', 'metadata.storage_needs_he') in data_full['replies']:
                storage_needs_id = \
                    data_full['replies'][config.get('Paths', 'metadata.storage_needs_he')][
                        'value'][
                        'value']
                if storage_needs_id == config.get('Paths', 'metadata.storage_needs.very_small_he'):
                    storage_needs = 'Very small, < 1 TB'
                elif storage_needs_id == config.get('Paths', 'metadata.storage_needs.small_he'):
                    storage_needs = 'Small (1-5 TB)'
                elif storage_needs_id == config.get('Paths', 'metadata.storage_needs.large_he'):
                    storage_needs = 'Large (5-50 TB)'
                elif storage_needs_id == config.get('Paths', 'metadata.storage_needs.very_large_he'):
                    storage_needs = 'Very large (>= 50 TB)'
                else:
                    storage_needs = 'unknown'
                md['storage_needs'] = storage_needs


        if '10a10ffd-bfe1-4c6b-bbb6-3dfb1e63a5d5.1cc02007-5443-49f2-ba73-216d6f5b1f4f' in data_full['replies']:
            storage_needs_id = \
                data_full['replies']['10a10ffd-bfe1-4c6b-bbb6-3dfb1e63a5d5.1cc02007-5443-49f2-ba73-216d6f5b1f4f'][
                    'value'][
                    'value']
            if storage_needs_id == '863fec5d-9338-402e-8363-cbc60a657a6b':
                storage_needs = 'Very small, < 1 TB'
            elif storage_needs_id == '936f61af-b5f9-4064-874f-3aec8661783d':
                storage_needs = 'Small (1-5 TB)'
            elif storage_needs_id == '0d5ca8f6-1b7d-4e8c-8286-890adeac89d0':
                storage_needs = 'Large (5-50 TB)'
            elif storage_needs_id == 'd190654f-d9b5-4f1f-993c-8b8324a1447a':
                storage_needs = 'Very large (>= 50 TB)'
            else:
                storage_needs = 'unknown'
            md['storage_needs'] = storage_needs

        # Ethical review
        ethical_review_needed = 'no'

        if config.get('Paths', 'ethical_review_applied') in data_full['replies']:
            print("ethical found")
            if \
                    data_full['replies'][config.get('Paths', 'ethical_review_applied')][
                        'value']['value'] == config.get('Paths', 'ethical_review_applied.yes'):
                ethical_review_needed = 'yes'
            if config.get('Paths', 'ethical_review_categories') in data_full['replies']:
                if \
                    data_full['replies'][config.get('Paths', 'ethical_review_categories')][
                        'value']['value'] == config.get('Paths', 'ethical_review_categories.yes'):
                    ethical_review_needed = 'yes'
            if config.get('Paths', 'ethical_review_support') in data_full['replies']:
                if \
                        data_full['replies'][config.get('Paths', 'ethical_review_support')][
                            'value']['value'] == config.get('Paths', 'ethical_review_support.yes'):
                    ethical_review_needed = 'yes'
            md['ethical_review_needed'] = ethical_review_needed
        
        dmp['dmp'] = d
        dmp['metadata'] = md

        # convert to json
        dmp_json = json.dumps(dmp)

        # debug
        #print(dmp_json)

        # PUT (POST) update to ES index
        # https://www.elastic.co/guide/en/elasticsearch/client/python-api/current/index.html
        # https://kb.objectrocket.com/elasticsearch/how-to-index-elasticsearch-documents-using-the-python-client-library

        if import_this == 'true':
            try:
                response = elastic.index(index=esindex, doc_type='dmp', id=dmp_id, document=dmp, ignore=[400, 404])
                # print(str(dmp))
                print('response: ' + str(response))
                print(dmp_id + ' was imported!')
            except elasticsearch.exceptions.RequestError as e:
                if e.error == 'resource_already_exists_exception':
                    print(dmp_id + ' already exists.')
                    pass  # Doc already exists. Ignore, it will be updated.
                else:  # Other exception - raise it
                    logger.error('Error when writing doc id: ' + dmp_id + ' to index.' + e.error)
                    print('Error when writing doc id: ' + dmp_id + ' to index.' + e.error)
                    raise e
                    sys.exit()
        else:
            print('DMP id ' + str(dmp_id) + ' was NOT imported.')

        print('\n')
        count += 1

logger.warning('Successfully indexed ' + str(count) + ' items.')
print('Successfully indexed ' + str(count) + ' items. Exiting now.')
sys.exit()
