import json, sys
p = '../../agent/cache/profile_fields.json'
d = json.load(open(p, encoding='utf8'))
done = sys.argv[1] == "done"
d['_meta']['complete'] = done
if not done:
    d['_meta']['status_en'] = sys.argv[2]
    d['_meta']['status_de'] = sys.argv[3]
json.dump(d, open(p, 'w', encoding='utf8'), ensure_ascii=False, indent=1)
