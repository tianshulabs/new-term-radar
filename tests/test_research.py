from radar import research


def test_exact_model_and_repo_sources(cfg):
    assert research.source_urls({'term':'x','kinds':['hf'],'examples':['owner/Model-2 (text-generation)']}, cfg) == [('https://huggingface.co/owner/Model-2','https://huggingface.co/owner/Model-2/raw/main/README.md')]
    assert research.source_urls({'term':'x','kinds':['x'],'examples':['@person: thing']}, cfg) == []


def test_invalid_citation_cannot_become_researched():
    item = {'term':'x','sources':[{'source_id':'s0','kind':'post','url':'https://x.com/a','text':'hello'}],'fetch_errors':[]}
    import json
    response = dict(term='x',what='Thing',use='Use',relevance='Topic',caveat='Claim',status='documented',citations=['wrong'])
    assert research.parse(json.dumps([response]), [item])[0]['status'] == 'unresolved'
    response['citations'] = ['s0']
    assert research.parse(json.dumps([response]), [item])[0]['status'] == 'limited'
    assert research.parse('not json', [item])[0]['status'] == 'unresolved'


def test_partial_research_keeps_every_candidate(cfg, monkeypatch):
    monkeypatch.setattr(research, 'fetch', lambda url: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(research.aisa, 'chat', lambda *a, **kw: (_ for _ in ()).throw(ValueError()))
    rows = [{'term':str(i), 'kinds':['hf'], 'examples':['owner/model (task)'], 'named':.9} for i in range(23)]
    rows.append({'term':'noise','named':.1})
    result = research.enrich(cfg, rows)
    assert len(result['items']) == 23
    assert all(r['status'] == 'unresolved' for r in result['items'])
    assert '23' in research.render(result)


def test_enrich_does_not_touch_discovery_state(tmp_path, cfg, monkeypatch):
    from radar import __main__ as cli
    from argparse import Namespace
    import json
    src = tmp_path/'report.json'; src.write_text(json.dumps({'leads':[]}))
    src.with_suffix('.md').write_text('Original report')
    out = tmp_path/'enhanced.md'
    monkeypatch.setattr(research, 'enrich', lambda *a: {'items':[], 'highlights':[]})
    cli.cmd_enrich(Namespace(input=str(src),out=str(out),mail=False), cfg)
    assert out.read_text() == 'Original report'
    assert json.loads(out.with_suffix('.json').read_text())['original_md'] == 'Original report'
    assert not (tmp_path/'state').exists()


def test_source_ids_are_scoped_to_each_candidate():
    import json
    items = [{'term':term,'sources':[{'source_id':sid,'kind':'author_documentation','url':'https://example.com/'+term}],'fetch_errors':[]} for term,sid in [('a','id-a'),('b','id-b')]]
    def answer(term, sid):
        return dict(term=term, what='identity', use='use', relevance='topic', caveat='author claim', status='documented', citations=[sid])
    parsed = research.parse(json.dumps([answer('a','id-a'),answer('b','id-b')]), items)
    assert [x['citations'] for x in parsed] == [[0],[0]]
    crossed = research.parse(json.dumps([answer('a','id-b'),answer('b','id-a')]), items)
    assert all(x['status'] == 'unresolved' for x in crossed)


def test_new_seller_reuses_already_fetched_description(cfg):
    row = {'term': 'sample', 'kinds': ['seller'], 'about': ('https://sample.example/', 'AI video editor')}
    item = research.evidence(row, cfg)
    assert item['sources'][0]['text'] == 'AI video editor'
    assert item['sources'][0]['source_id']


def test_hf_space_document_survives_discovery_merge(cfg):
    from radar import sources, report
    cfg['judge']['model'] = ''
    terms = sources._entered([{'id': 'maker/NewDemo', 'sdk': 'gradio'}], [], 'spaces')
    rows, _ = report.leads(cfg, {'hf': {'terms': terms}})
    assert research.source_urls(rows[0], cfg) == [('https://huggingface.co/spaces/maker/NewDemo', 'https://huggingface.co/spaces/maker/NewDemo/raw/main/README.md')]
