def get_conditions_tuple():
    return (
        ('new', 'New'),
        ('usedlikenew', 'UsedLikeNew'),
        ('usedverygood', 'UsedVeryGood'),
        ('usedgood', 'UsedGood'),
        ('usedacceptable', 'UsedAcceptable'),
        ('collectiblelikenew', 'CollectibleLikeNew'),
        ('collectibleverygood', 'CollectibleVeryGood'),
        ('collectiblegood', 'CollectibleGood'),
        ('collectibleacceptable', 'CollectibleAcceptable'),
        ('refurbished', 'Refurbished'),
        ('club', 'Club')
    )


def normalize_condition(condition):
    if condition is None:
        return
    return condition.replace(' ', '').lower()


def mws_normalize_condition(condition):
    conditions = dict(get_conditions_tuple())
    return conditions[condition]
