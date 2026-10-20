"""Parse old and new system_profiler USB dictionaries; Python 2.7 and 3."""
try:
    STRING_TYPES = (basestring,)
except NameError:
    STRING_TYPES = (str,)


def parse(value):
    found = []
    def visit(item):
        if isinstance(item, dict):
            name = item.get('_name', '')
            serial = item.get('serial_num') or item.get('d_serial_num')
            if isinstance(name, STRING_TYPES) and any(x in name.lower() for x in ('iphone', 'ipad', 'ipod')) and serial:
                found.append({'name': name, 'serial': serial,
                    'productId': item.get('product_id') or item.get('a_product_id'),
                    'vendorId': item.get('vendor_id') or item.get('b_vendor_id'),
                    'speed': item.get('device_speed') or item.get('e_device_speed')})
            for child in item.values(): visit(child)
        elif isinstance(item, list):
            for child in item: visit(child)
    visit(value)
    return found
