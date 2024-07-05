def convert_to_bytes(size_string):
    size_string = str(size_string).upper()
    if size_string.isdigit():
        return int(size_string)
    
    units = {
        'B': 1,
        'KB': 1000,
        'MB': 1000**2,
        'GB': 1000**3,
        'TB': 1000**4,
        'PB': 1000**5,
        'KIB': 1024,
        'MIB': 1024**2,
        'GIB': 1024**3,
        'TIB': 1024**4,
        'PIB': 1024**5
    }
    
    number = float(''.join(filter(lambda x: x.isdigit() or x == '.', size_string)))
    unit = ''.join(filter(lambda x: x.isalpha(), size_string))
    
    if unit not in units:
        raise ValueError(f"Unknown unit: {unit}")
    
    return int(number * units[unit])

class FilterModule(object):
    def filters(self):
        return {
            'convert_to_bytes': convert_to_bytes
        }