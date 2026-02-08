appointments = []

def book_appointment(name, date, time):
    appointments.append({"name": name, "date": date, "time": time})
    return True
