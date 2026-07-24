from app.exporter import _event_splits


def test_event_group_splits_keep_controls_with_events():
    rows=[]
    for i in range(10):
        rows.append({'sample_id':f'e{i}','label':'event','event_id':str(i),'anchor_time':f'2026-01-{i+1:02d}'})
        rows.append({'sample_id':f'c{i}','label':'control','event_id':str(i),'anchor_time':f'2025-12-{i+1:02d}'})
    splits=_event_splits(rows)
    assert len(splits['discovery'])==6
    assert len(splits['validation'])==2
    assert len(splits['sealed_test'])==2
    assert set.union(*splits.values())=={str(i) for i in range(10)}
